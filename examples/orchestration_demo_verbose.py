#!/usr/bin/env python3
"""
ZyndAI Orchestration Demo — Verbose Mode (for recording)

Shows real-time logs of every step: agent boot, message signing,
HTTP dispatch, parallel fan-out, result synthesis — like watching
Claude Code work.

Record with:
    brew install asciinema
    asciinema rec demo.cast -c "uv run python examples/orchestration_demo_verbose.py"
    # Upload: asciinema upload demo.cast

Or screen record:
    uv run python examples/orchestration_demo_verbose.py
"""

import asyncio
import json
import logging
import os
import sys
import time
import tempfile
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Silence all library noise
logging.getLogger("werkzeug").setLevel(logging.ERROR)
logging.getLogger("WebhookAgentCommunication").setLevel(logging.ERROR)
logging.getLogger("urllib3").setLevel(logging.ERROR)

import io
import builtins

_real_print = builtins.print


class _QuietContext:
    def __enter__(self):
        self._stdout, self._stderr = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
        return self
    def __exit__(self, *a):
        sys.stdout, sys.stderr = self._stdout, self._stderr


from zyndai_agent.ed25519_identity import generate_keypair, save_keypair
from zyndai_agent import (
    ZyndAIAgent, AgentConfig, InvokeMessage,
    parse_message, sign_message, verify_message, dns_registry,
)
from zyndai_agent.orchestration.task import TaskTracker
from zyndai_agent.orchestration.coordinator import _format_result_for_briefing
from zyndai_agent.session import AgentSession
from zyndai_agent.typed_messages import generate_id

REGISTRY_URL = os.getenv("REGISTRY_URL", "https://dns01.zynd.ai")
TOPIC = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "AI agents that discover and pay each other using micropayments"

_llm = None
try:
    import openai
    if os.getenv("OPENAI_API_KEY"):
        _llm = openai.OpenAI()
except ImportError:
    pass

AI_MODE = "GPT-4o-mini" if _llm else "Built-in Logic"


# ═══════════════════════════════════════════════════════════════════════════════
#  Live Logger — the core of the verbose demo
# ═══════════════════════════════════════════════════════════════════════════════

DIM    = "\033[2m"
BOLD   = "\033[1m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
CYAN   = "\033[36m"
RED    = "\033[31m"
MAGENTA= "\033[35m"
RESET  = "\033[0m"
BG_DARK = "\033[48;5;236m"

AGENT_COLORS = {
    "SYSTEM":      f"{BOLD}{CYAN}",
    "COORDINATOR": f"{BOLD}{MAGENTA}",
    "RESEARCHER":  f"{BOLD}{GREEN}",
    "ANALYST":     f"{BOLD}{YELLOW}",
    "WRITER":      f"{BOLD}{CYAN}",
}

_t0 = time.time()


def log(agent: str, msg: str, detail: str = "", indent: bool = False):
    """Print a timestamped log line like Claude Code's output."""
    elapsed = time.time() - _t0
    ts = f"{elapsed:6.1f}s"
    color = AGENT_COLORS.get(agent.upper(), DIM)
    prefix = f"  {DIM}{ts}{RESET}  {color}{agent:14s}{RESET}"
    if indent:
        prefix = f"  {DIM}{' ':6s}{RESET}  {' ':14s}"
    _real_print(f"{prefix}  {msg}")
    if detail:
        for line in detail.split("\n"):
            _real_print(f"  {DIM}{' ':6s}{RESET}  {' ':14s}  {DIM}{line}{RESET}")
    time.sleep(0.05)  # Slight delay so the video shows lines appearing


def log_divider(label: str = ""):
    _real_print()
    if label:
        _real_print(f"  {BOLD}{'─' * 3} {label} {'─' * max(0, 55 - len(label))}{RESET}")
    else:
        _real_print(f"  {DIM}{'─' * 64}{RESET}")
    _real_print()


# ═══════════════════════════════════════════════════════════════════════════════
#  LLM Backend
# ═══════════════════════════════════════════════════════════════════════════════

def ask_llm(agent_name: str, system: str, prompt: str, max_tokens: int = 300) -> str:
    if _llm:
        log(agent_name, f"Calling {AI_MODE}...")
        resp = _llm.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            max_tokens=max_tokens, temperature=0.7,
        )
        answer = resp.choices[0].message.content.strip()
        tokens = resp.usage.total_tokens if resp.usage else 0
        log(agent_name, f"LLM responded ({tokens} tokens)")
        return answer
    return ""


# ═══════════════════════════════════════════════════════════════════════════════
#  Agent Factory
# ═══════════════════════════════════════════════════════════════════════════════

def make_kp(tmpdir, name):
    kp = generate_keypair()
    p = os.path.join(tmpdir, f"{name}.json")
    save_keypair(kp, p)
    return p


def base_url(agent):
    return agent.webhook_url.replace("/webhook", "")


def boot_agent(tmpdir, name, desc, skills, category, port):
    log("SYSTEM", f"Booting {name}...", f"port={port}  skills={skills}")
    agent_dir = os.path.join(tmpdir, name)
    os.makedirs(agent_dir, exist_ok=True)
    with _QuietContext():
        agent = ZyndAIAgent(AgentConfig(
            name=name, description=desc,
            capabilities={"skills": skills},
            category=category, summary=desc[:200], tags=skills,
            webhook_port=port, config_dir=agent_dir,
            registry_url=REGISTRY_URL,
        ))

    url = base_url(agent)
    log("SYSTEM", f"{GREEN}✓{RESET} {name} online at {CYAN}{url}{RESET}",
        f"id={agent.agent_id}  key={agent.keypair.public_key_b64[:16]}...")

    # Update agent_url on registry
    dns_registry.update_agent(REGISTRY_URL, agent.agent_id, agent.keypair, {"agent_url": url})

    return agent


# ═══════════════════════════════════════════════════════════════════════════════
#  Worker Handlers (with verbose logging)
# ═══════════════════════════════════════════════════════════════════════════════

def _extract(msg):
    try:
        t = parse_message(msg.to_dict())
        if hasattr(t, "payload") and isinstance(t.payload, dict):
            return t.payload.get("content") or t.payload.get("task") or msg.content or ""
    except Exception:
        pass
    return msg.content or ""


def make_handler(agent, agent_label, system_prompt, fallback_result):
    def handler(msg, topic, session):
        task = _extract(msg)
        log(agent_label, f"Received message from {msg.sender_id[:20]}...")
        log(agent_label, f"Task: \"{task[:80]}{'...' if len(task) > 80 else ''}\"")

        if _llm:
            answer = ask_llm(
                agent_label, system_prompt,
                f"Return ONLY valid JSON (no markdown). {task}",
            )
            answer = answer.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            try:
                result = json.loads(answer)
            except json.JSONDecodeError:
                result = fallback_result
        else:
            log(agent_label, "Processing with built-in logic...")
            delay = 0.5 + (hash(agent_label) % 5) / 10
            time.sleep(delay)
            result = fallback_result

        keys = list(result.keys())
        log(agent_label, f"{GREEN}✓{RESET} Done — returning {keys}")
        agent.set_response(msg.message_id, json.dumps(result))

    return handler


# ═══════════════════════════════════════════════════════════════════════════════
#  Orchestration with live logging
# ═══════════════════════════════════════════════════════════════════════════════

async def call_worker_verbose(coordinator, session, tracker, name, url, task_desc, cost=0.001):
    label = name.upper()

    log("COORDINATOR", f"Building InvokeMessage for {label}")
    msg = InvokeMessage(
        conversation_id=session.conversation_id,
        sender_id=coordinator.agent_id,
        sender_public_key=coordinator.keypair.public_key_string,
        capability=name,
        payload={"task": task_desc, "content": task_desc},
        timeout_seconds=30,
    )

    log("COORDINATOR", f"Signing message with Ed25519...")
    msg.signature = sign_message(msg, coordinator.keypair.private_key)
    sig_preview = msg.signature[:30]
    log("COORDINATOR", f"Signature: {DIM}{sig_preview}...{RESET}")

    log("COORDINATOR", f"POST {CYAN}{url}/webhook/sync{RESET} → {label}")

    task = tracker.create_task(description=f"[{name}] {task_desc[:50]}...", assigned_to=url)
    task.mark_running()

    try:
        resp = await asyncio.to_thread(
            coordinator.x402_processor.session.post,
            f"{url}/webhook/sync",
            json=msg.model_dump(mode="json"),
            timeout=30,
        )
        data = resp.json()
        if resp.status_code == 200 and data.get("status") == "success":
            raw = data.get("response", "{}")
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            task.mark_completed(parsed, {"cost_usd": cost, "duration_ms": task.duration_ms})
            log("COORDINATOR", f"{GREEN}✓{RESET} {label} responded in {task.duration_ms:.0f}ms")
            return {"status": "success", "agent": name, "result": parsed, "duration_ms": task.duration_ms}
        else:
            err = data.get("error", f"HTTP {resp.status_code}")
            task.mark_failed(err)
            log("COORDINATOR", f"{RED}✗{RESET} {label} failed: {err}")
            return {"status": "error", "agent": name, "error": err}
    except Exception as e:
        task.mark_failed(str(e))
        log("COORDINATOR", f"{RED}✗{RESET} {label} error: {e}")
        return {"status": "error", "agent": name, "error": str(e)}


async def run_pipeline(coordinator, worker_urls, topic):
    session = AgentSession(conversation_id=generate_id())
    tracker = TaskTracker()

    log_divider("PHASE 1: PARALLEL RESEARCH + ANALYSIS")

    log("COORDINATOR", f"Received task: \"{topic}\"")
    log("COORDINATOR", "Breaking into 2 parallel subtasks...")
    log("COORDINATOR", f"  → RESEARCHER: \"Research the topic: {topic[:50]}...\"")
    log("COORDINATOR", f"  → ANALYST: \"Analyze trends for: {topic[:50]}...\"")
    log("COORDINATOR", f"Dispatching both via asyncio.gather (parallel fan-out)...")

    # Suppress "Incoming Message" prints from Flask threads
    _orig = builtins.print
    builtins.print = lambda *a, **k: None if "Incoming" in " ".join(str(x) for x in a) else _orig(*a, **k)

    p1_start = time.time()

    researcher_result, analyst_result = await asyncio.gather(
        call_worker_verbose(coordinator, session, tracker, "researcher",
                            worker_urls["researcher"], f"Research the topic: {topic}"),
        call_worker_verbose(coordinator, session, tracker, "analyst",
                            worker_urls["analyst"], f"Analyze market trends and data for: {topic}"),
    )

    p1_ms = (time.time() - p1_start) * 1000
    log("COORDINATOR", f"Phase 1 complete: both agents responded in {p1_ms:.0f}ms (parallel)")

    # Show results
    for r in [researcher_result, analyst_result]:
        if r["status"] == "success":
            result = r["result"]
            log("COORDINATOR", f"Reading {r['agent'].upper()} results:")
            for key, val in result.items():
                if isinstance(val, list):
                    for item in val:
                        log("COORDINATOR", f"  • {item}", indent=True)
                else:
                    log("COORDINATOR", f"  {key}: {val}", indent=True)

    log_divider("PHASE 2: SYNTHESIS + REPORT WRITING")

    log("COORDINATOR", "Synthesizing Phase 1 results into briefing...")

    research_data = researcher_result.get("result", {}) if researcher_result["status"] == "success" else {}
    analysis_data = analyst_result.get("result", {}) if analyst_result["status"] == "success" else {}

    briefing_parts = []
    if research_data:
        briefing_parts.append(f"[Research]\n{_format_result_for_briefing(research_data)}")
    if analysis_data:
        briefing_parts.append(f"[Analysis]\n{_format_result_for_briefing(analysis_data)}")
    briefing = "\n\n".join(briefing_parts) or "No data."

    log("COORDINATOR", f"Briefing ready ({len(briefing)} chars)")
    for line in briefing.split("\n")[:6]:
        log("COORDINATOR", f"  {DIM}{line}{RESET}", indent=True)
    if briefing.count("\n") > 6:
        log("COORDINATOR", f"  {DIM}... ({briefing.count(chr(10)) - 6} more lines){RESET}", indent=True)

    log("COORDINATOR", f"Sending briefing to WRITER for final summary...")

    p2_start = time.time()
    writer_result = await call_worker_verbose(
        coordinator, session, tracker, "writer",
        worker_urls["writer"],
        f"Write an executive summary about '{topic}'. "
        f"Here is the synthesized research and analysis:\n\n{briefing}",
        cost=0.002,
    )
    p2_ms = (time.time() - p2_start) * 1000

    builtins.print = _orig

    total_ms = (time.time() - (p1_start - 0.001)) * 1000

    return {
        "researcher": researcher_result,
        "analyst": analyst_result,
        "writer": writer_result,
        "p1_ms": p1_ms, "p2_ms": p2_ms, "total_ms": total_ms,
        "task_summary": tracker.summary(),
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    global _t0
    _t0 = time.time()

    _real_print()
    _real_print(f"  {BOLD}{'═' * 64}{RESET}")
    _real_print(f"  {BOLD}  ZyndAI Multi-Agent Orchestration{RESET}")
    _real_print(f"  {BOLD}{'═' * 64}{RESET}")
    _real_print()

    log("SYSTEM", f"Topic: \"{topic}\"" if (topic := TOPIC) else "")
    log("SYSTEM", f"AI Backend: {AI_MODE}")
    log("SYSTEM", f"Registry: {REGISTRY_URL}")
    log("SYSTEM", f"Protocol: Ed25519 signed messages over HTTP webhooks")

    tmpdir = tempfile.mkdtemp(prefix="zyndai_demo_")

    log_divider("BOOTING 4 AGENTS")

    agents = {}
    defs = [
        ("coordinator", "Orchestrates multi-agent pipelines",    ["orchestration"],           "orchestration", 7200),
        ("researcher",  "Finds information and key facts",       ["research", "web-search"],  "research",      7201),
        ("analyst",     "Analyzes trends and identifies risks",  ["analysis", "data"],        "analysis",      7202),
        ("writer",      "Writes executive summaries",            ["writing", "summarization"],"content",       7203),
    ]
    for name, desc, skills, cat, port in defs:
        agents[name] = boot_agent(tmpdir, name, desc, skills, cat, port)

    # Register handlers
    agents["researcher"].register_handler(make_handler(
        agents["researcher"], "RESEARCHER",
        "You are a research agent. Return ONLY valid JSON with: findings (list of 3 strings), sources_consulted (int), confidence (float 0-1).",
        {"findings": ["Market projected at $150B by 2028", "Three adoption waves: dev tools, enterprise APIs, consumer agents", "x402 protocol seeing 300% YoY growth"], "sources_consulted": 14, "confidence": 0.87},
    ))
    agents["analyst"].register_handler(make_handler(
        agents["analyst"], "ANALYST",
        "You are a data analyst. Return ONLY valid JSON with: trends (list of 3 strings), risk_score (float 0-1), opportunity_score (float 0-1), data_points_analyzed (int).",
        {"trends": ["Shift to micro-agent architectures accelerating", "Agent-to-agent payment volume doubled", "Developer adoption at S-curve inflection"], "risk_score": 0.25, "opportunity_score": 0.91, "data_points_analyzed": 1247},
    ))
    agents["writer"].register_handler(make_handler(
        agents["writer"], "WRITER",
        "You are an executive report writer. Return ONLY valid JSON with: summary (string, 4-5 sentences), word_count (int).",
        {"summary": "The agent infrastructure market is at an inflection point. Three dynamics converge: micro-agent architectures, maturing payment protocols (x402 at 300% YoY), and developer tooling reducing time-to-production to minutes. Market projections indicate $150B TAM by 2028, with risk at 0.25 and opportunity at 0.91. The moat is developer experience and network density — whoever gets agents talking to each other first, wins.", "word_count": 62},
    ))

    # Wait for health
    for name, agent in agents.items():
        url = base_url(agent)
        for _ in range(30):
            try:
                if requests.get(f"{url}/health", timeout=2).status_code == 200:
                    break
            except requests.ConnectionError:
                pass
            time.sleep(0.2)

    log_divider("AGENT DISCOVERY VIA REGISTRY")

    log("COORDINATOR", f"Searching registry at {CYAN}{REGISTRY_URL}{RESET}...")

    worker_urls = {}
    coordinator = agents["coordinator"]
    for worker_name, keyword in [("researcher", "research"), ("analyst", "analysis"), ("writer", "writing")]:
        try:
            found = coordinator.search_agents(keyword=keyword, limit=5)
            match = next((a for a in found if a.get("name") == worker_name), None)
            if match:
                agent_id = match["agent_id"]
                full = dns_registry.get_agent(REGISTRY_URL, agent_id)
                url = (full or {}).get("agent_url", "") or base_url(agents[worker_name])
                worker_urls[worker_name] = url
                score = match.get("score", 0)
                log("COORDINATOR", f'{GREEN}✓{RESET} search("{keyword}") → {BOLD}{match["name"]}{RESET}  score={score:.2f}  url={url}')
            else:
                worker_urls[worker_name] = base_url(agents[worker_name])
                log("COORDINATOR", f'{YELLOW}⏱{RESET} search("{keyword}") → resolving locally')
        except Exception as e:
            worker_urls[worker_name] = base_url(agents[worker_name])
            log("COORDINATOR", f'{YELLOW}⏱{RESET} search("{keyword}") → fallback')

    # Run orchestration
    results = asyncio.run(run_pipeline(coordinator, worker_urls, TOPIC))

    # Final output
    log_divider("EXECUTIVE SUMMARY")

    if results["writer"]["status"] == "success":
        summary = results["writer"]["result"].get("summary", "")
        import textwrap
        for line in textwrap.wrap(summary, 64):
            _real_print(f"    {line}")
    else:
        _real_print(f"    {RED}Writer failed: {results['writer'].get('error')}{RESET}")

    log_divider("PIPELINE REPORT")

    ts = results["task_summary"]
    log("SYSTEM", f"Tasks: {ts['total']} total, {ts['by_status'].get('completed', 0)} completed, {ts['by_status'].get('failed', 0)} failed")
    log("SYSTEM", f"Cost: ${ts['total_cost_usd']:.4f} USDC")
    log("SYSTEM", f"Phase 1 (parallel): {results['p1_ms']:.0f}ms")
    log("SYSTEM", f"Phase 2 (sequential): {results['p2_ms']:.0f}ms")
    log("SYSTEM", f"Total: {results['total_ms']:.0f}ms")

    r_ms = results["researcher"].get("duration_ms", 0) or 0
    a_ms = results["analyst"].get("duration_ms", 0) or 0
    w_ms = results["writer"].get("duration_ms", 0) or 0
    seq = r_ms + a_ms + w_ms
    if seq > 0:
        log("SYSTEM", f"Parallelism: {seq / results['total_ms']:.1f}x faster than sequential")

    log_divider("SECURITY PROOF")

    msg = InvokeMessage(
        sender_id=coordinator.agent_id,
        sender_public_key=coordinator.keypair.public_key_string,
        capability="proof", payload={"signed": True},
    )
    msg.signature = sign_message(msg, coordinator.keypair.private_key)
    ok = verify_message(msg, coordinator.keypair.public_key_b64)
    log("SYSTEM", f"Ed25519 signature verified: {GREEN}{'True' if ok else 'False'}{RESET}")

    tampered = InvokeMessage(**msg.model_dump())
    tampered.payload = {"signed": False}
    bad = verify_message(tampered, coordinator.keypair.public_key_b64)
    log("SYSTEM", f"Tampered message rejected: {GREEN}{'True' if not bad else 'False'}{RESET}")

    log_divider("SESSIONS")

    for name, agent in agents.items():
        s = agent.active_sessions
        msgs = sum(len(x.messages) for x in s)
        log("SYSTEM", f"{name:14s}  {len(s)} session(s), {msgs} message(s)")

    _real_print()
    for a in agents.values():
        a.stop_heartbeat()

    _real_print(f"  {BOLD}{'═' * 64}{RESET}")
    _real_print(f"  {BOLD}  Demo Complete{RESET}")
    _real_print(f"  {BOLD}{'═' * 64}{RESET}")
    _real_print()
    _real_print(f"  {DIM}Record this demo:{RESET}")
    _real_print(f"  {DIM}  brew install asciinema{RESET}")
    _real_print(f'  {DIM}  asciinema rec demo.cast -c "uv run python examples/orchestration_demo_verbose.py"{RESET}')
    _real_print(f"  {DIM}  asciinema upload demo.cast{RESET}")
    _real_print()


if __name__ == "__main__":
    main()
