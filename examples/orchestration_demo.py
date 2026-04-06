#!/usr/bin/env python3
"""
ZyndAI Orchestration Demo — 4 Real Agents, Full Pipeline

Shows a manager what the platform does:
  - 4 independent agent services start up with cryptographic identity
  - A coordinator receives a task and breaks it into subtasks
  - Subtasks are dispatched to specialist agents IN PARALLEL over HTTP
  - Each agent signs its messages with Ed25519
  - Results flow back, get synthesized into a final report
  - Every step is tracked: timing, cost, status

Run:
    cd zyndai-agent
    uv run python examples/orchestration_demo.py

    # Custom topic:
    uv run python examples/orchestration_demo.py "your topic here"

If OPENAI_API_KEY is set, agents use real GPT-4o-mini.
Otherwise, agents use built-in domain logic (no API key needed).
"""

import asyncio
import json
import logging
import os
import sys
import textwrap
import time
import tempfile
import requests
from dotenv import load_dotenv

load_dotenv()

# Silence all library noise — this demo controls its own output
logging.getLogger("werkzeug").setLevel(logging.ERROR)
logging.getLogger("WebhookAgentCommunication").setLevel(logging.ERROR)
logging.getLogger("urllib3").setLevel(logging.ERROR)

import io
import builtins
_real_print = builtins.print

class _QuietContext:
    """Suppress all stdout/stderr during agent boot — SDK is very noisy."""
    def __enter__(self):
        self._stdout = sys.stdout
        self._stderr = sys.stderr
        sys.stdout = io.StringIO()
        sys.stderr = io.StringIO()
        return self
    def __exit__(self, *args):
        sys.stdout = self._stdout
        sys.stderr = self._stderr

print = _real_print

from zyndai_agent.ed25519_identity import generate_keypair, save_keypair
from zyndai_agent import (
    ZyndAIAgent,
    AgentConfig,
    InvokeMessage,
    parse_message,
    sign_message,
    verify_message,
)
from zyndai_agent.orchestration.task import TaskTracker
from zyndai_agent.session import AgentSession
from zyndai_agent.typed_messages import generate_id

REGISTRY_URL = os.getenv("REGISTRY_URL", "https://dns01.zynd.ai")

# Try to load OpenAI for real AI responses
_llm = None
try:
    import openai
    if os.getenv("OPENAI_API_KEY"):
        _llm = openai.OpenAI()
except ImportError:
    pass

AI_MODE = "GPT-4o-mini" if _llm else "Built-in Logic"


# ═══════════════════════════════════════════════════════════════════════════════
#  Terminal UI
# ═══════════════════════════════════════════════════════════════════════════════

DIM    = "\033[2m"
BOLD   = "\033[1m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
CYAN   = "\033[36m"
RED    = "\033[31m"
RESET  = "\033[0m"
CHECK  = f"{GREEN}✓{RESET}"
CROSS  = f"{RED}✗{RESET}"
ARROW  = f"{CYAN}→{RESET}"
CLOCK  = f"{YELLOW}⏱{RESET}"


def banner(text: str):
    w = 70
    print()
    print(f"{BOLD}{'═' * w}{RESET}")
    print(f"{BOLD}  {text}{RESET}")
    print(f"{BOLD}{'═' * w}{RESET}")


def section(text: str):
    print(f"\n{BOLD}  ── {text} {'─' * max(0, 60 - len(text))}{RESET}\n")


def status(icon: str, label: str, detail: str = ""):
    print(f"    {icon}  {label}{DIM}  {detail}{RESET}" if detail else f"    {icon}  {label}")


def kvline(key: str, val: str):
    print(f"    {DIM}{key:18s}{RESET} {val}")


def wrap_text(text: str, width: int = 64, indent: str = "    "):
    for line in textwrap.wrap(text, width):
        print(f"{indent}{line}")


def fmt_ms(ms: float) -> str:
    if ms < 1000:
        return f"{ms:.0f}ms"
    return f"{ms / 1000:.1f}s"


def progress(label: str):
    sys.stdout.write(f"    {DIM}⋯ {label}...{RESET}")
    sys.stdout.flush()


def progress_done(detail: str):
    sys.stdout.write(f"\r    {CHECK}  {detail}\n")
    sys.stdout.flush()


# ═══════════════════════════════════════════════════════════════════════════════
#  AI / Simulation Backend
# ═══════════════════════════════════════════════════════════════════════════════

def ask_llm(system_prompt: str, user_prompt: str, max_tokens: int = 300) -> str:
    if _llm:
        resp = _llm.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            temperature=0.7,
        )
        return resp.choices[0].message.content.strip()
    return ""


# ═══════════════════════════════════════════════════════════════════════════════
#  Agent Factory
# ═══════════════════════════════════════════════════════════════════════════════

def make_keypair_file(tmpdir: str, name: str) -> str:
    kp = generate_keypair()
    path = os.path.join(tmpdir, f"{name}.json")
    save_keypair(kp, path)
    return path


def agent_base_url(agent: ZyndAIAgent) -> str:
    return agent.webhook_url.replace("/webhook", "")


def wait_healthy(url: str, label: str):
    for _ in range(30):
        try:
            if requests.get(f"{url}/health", timeout=2).status_code == 200:
                return
        except requests.ConnectionError:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"{label} at {url} never started")


def create_agent(tmpdir, name, desc, skills, category, port):
    agent_dir = os.path.join(tmpdir, name)
    os.makedirs(agent_dir, exist_ok=True)
    with _QuietContext():
        agent = ZyndAIAgent(AgentConfig(
            name=name,
            description=desc,
            capabilities={"skills": skills},
            category=category,
            summary=desc[:200],
            tags=skills,
            webhook_port=port,
            config_dir=agent_dir,
            registry_url=REGISTRY_URL,
        ))

    # AgentDNS doesn't persist agent_url from registration (server bug).
    # Push it via update so search results include the URL for discovery.
    from zyndai_agent import dns_registry
    base = agent_base_url(agent)
    dns_registry.update_agent(
        registry_url=REGISTRY_URL,
        agent_id=agent.agent_id,
        keypair=agent.keypair,
        updates={"agent_url": base},
    )
    return agent


# ═══════════════════════════════════════════════════════════════════════════════
#  Worker Handlers
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_task(msg) -> str:
    """Extract the task description from either typed or legacy message."""
    if msg.content:
        return msg.content
    if msg.metadata and msg.metadata.get("task"):
        return msg.metadata["task"]
    try:
        raw = msg.to_dict()
        typed = parse_message(raw)
        if hasattr(typed, "payload") and isinstance(typed.payload, dict):
            return typed.payload.get("task", "") or typed.payload.get("content", "")
    except Exception:
        pass
    return ""


def researcher_handler(agent):
    def handler(msg, topic, session):
        task = _extract_task(msg)

        if _llm:
            answer = ask_llm(
                "You are a research agent. You MUST return ONLY a valid JSON object (no markdown, no ```json blocks) with these exact keys: findings (list of 3 short strings), sources_consulted (int), confidence (float 0-1). Nothing else.",
                f"Research this topic and return structured findings as JSON: {task}",
            )
            answer = answer.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            try:
                result = json.loads(answer)
            except json.JSONDecodeError:
                result = {"findings": [answer[:200]], "sources_consulted": 5, "confidence": 0.8}
        else:
            time.sleep(0.8)
            result = {
                "findings": [
                    f"The market for '{task[:40]}' is projected to reach $150B by 2028",
                    "Three major adoption waves identified: developer tools, enterprise APIs, consumer agents",
                    "x402 micropayment protocol seeing 300% YoY growth across 15+ platforms",
                ],
                "sources_consulted": 14,
                "confidence": 0.87,
            }
        agent.set_response(msg.message_id, json.dumps(result))
    return handler


def analyst_handler(agent):
    def handler(msg, topic, session):
        task = _extract_task(msg)

        if _llm:
            answer = ask_llm(
                "You are a data analyst agent. You MUST return ONLY a valid JSON object (no markdown, no ```json blocks) with these exact keys: trends (list of 3 short strings), risk_score (float 0-1), opportunity_score (float 0-1), data_points_analyzed (int). Nothing else.",
                f"Analyze trends and risks for this topic, return JSON: {task}",
            )
            answer = answer.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            try:
                result = json.loads(answer)
            except json.JSONDecodeError:
                result = {"trends": [answer[:200]], "risk_score": 0.3, "opportunity_score": 0.85, "data_points_analyzed": 200}
        else:
            time.sleep(0.6)
            result = {
                "trends": [
                    "Shift from monolithic AI to micro-agent architectures accelerating",
                    "Agent-to-agent payment volume doubled in last quarter",
                    "Developer adoption curve following classic S-curve — currently at inflection point",
                ],
                "risk_score": 0.25,
                "opportunity_score": 0.91,
                "data_points_analyzed": 1247,
            }
        agent.set_response(msg.message_id, json.dumps(result))
    return handler


def writer_handler(agent):
    def handler(msg, topic, session):
        task = _extract_task(msg)

        if _llm:
            answer = ask_llm(
                "You are an executive report writer. You MUST return ONLY a valid JSON object (no markdown, no ```json blocks) with these exact keys: summary (string — a 4-5 sentence executive summary), word_count (int). Nothing else.",
                f"Write an executive summary about this topic using the provided research and analysis. Return JSON: {task}",
                max_tokens=500,
            )
            answer = answer.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            try:
                result = json.loads(answer)
            except json.JSONDecodeError:
                result = {"summary": answer, "word_count": len(answer.split())}
        else:
            time.sleep(0.5)
            result = {
                "summary": (
                    "The agent infrastructure market is at an inflection point. "
                    "Three key dynamics are converging: the shift to micro-agent architectures, "
                    "the maturation of agent-to-agent payment protocols (x402 seeing 300% YoY growth), "
                    "and developer tooling that reduces time-to-production from weeks to minutes. "
                    "Market projections indicate a $150B total addressable market by 2028, with "
                    "current risk assessment at 0.25/1.0 and opportunity at 0.91/1.0. "
                    "The primary moat will be developer experience and network density — "
                    "whoever gets agents talking to each other first, wins."
                ),
                "word_count": 89,
            }
        agent.set_response(msg.message_id, json.dumps(result))
    return handler


# ═══════════════════════════════════════════════════════════════════════════════
#  Orchestration Engine
# ═══════════════════════════════════════════════════════════════════════════════

async def call_worker(
    coordinator: ZyndAIAgent,
    session: AgentSession,
    tracker: TaskTracker,
    name: str,
    url: str,
    task_desc: str,
    cost_usd: float = 0.001,
) -> dict:
    """Send a signed InvokeMessage to a worker and wait for response."""
    msg = InvokeMessage(
        conversation_id=session.conversation_id,
        sender_id=coordinator.agent_id,
        sender_public_key=coordinator.keypair.public_key_string,
        capability=name,
        payload={"task": task_desc, "content": task_desc},
        timeout_seconds=30,
    )
    msg.signature = sign_message(msg, coordinator.keypair.private_key)

    task = tracker.create_task(description=f"[{name}] {task_desc[:60]}...", assigned_to=url)
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
            task.mark_completed(parsed, {"cost_usd": cost_usd, "duration_ms": task.duration_ms})
            return {"status": "success", "agent": name, "result": parsed, "duration_ms": task.duration_ms}
        else:
            err = data.get("error", f"HTTP {resp.status_code}")
            task.mark_failed(err)
            return {"status": "error", "agent": name, "error": err}
    except Exception as e:
        task.mark_failed(str(e))
        return {"status": "error", "agent": name, "error": str(e)}


async def run_pipeline(coordinator, worker_urls, topic):
    session = AgentSession(conversation_id=generate_id())
    tracker = TaskTracker()
    t0 = time.time()

    # ─── Phase 1: Parallel fan-out to researcher + analyst ────────────────

    section("PHASE 1  ·  Research + Analysis  (parallel fan-out)")

    progress("Dispatching to researcher and analyst simultaneously")
    p1_start = time.time()

    # Suppress "Incoming Message:" prints from worker Flask threads
    import builtins
    _orig_print = builtins.print
    def _quiet_print(*a, **kw):
        text = " ".join(str(x) for x in a)
        if "Incoming Message" in text:
            return
        _orig_print(*a, **kw)
    builtins.print = _quiet_print

    researcher_result, analyst_result = await asyncio.gather(
        call_worker(coordinator, session, tracker, "researcher",
                    worker_urls["researcher"],
                    f"Research the topic: {topic}"),
        call_worker(coordinator, session, tracker, "analyst",
                    worker_urls["analyst"],
                    f"Analyze market trends and data for: {topic}"),
    )

    p1_ms = (time.time() - p1_start) * 1000
    progress_done(f"Both agents responded in {fmt_ms(p1_ms)} (parallel)\n")

    for r in [researcher_result, analyst_result]:
        icon = CHECK if r["status"] == "success" else CROSS
        dur = fmt_ms(r.get("duration_ms", 0))
        status(icon, f'{r["agent"]:12s}', f"{dur}")
        if r["status"] == "success":
            result = r["result"]
            if "findings" in result:
                for f in result["findings"]:
                    print(f"          {DIM}• {f}{RESET}")
            if "trends" in result:
                for t in result["trends"]:
                    print(f"          {DIM}• {t}{RESET}")
            extras = []
            if "sources_consulted" in result:
                extras.append(f"{result['sources_consulted']} sources")
            if "confidence" in result:
                extras.append(f"{result['confidence']:.0%} confidence")
            if "data_points_analyzed" in result:
                extras.append(f"{result['data_points_analyzed']} data points")
            if "risk_score" in result:
                extras.append(f"risk {result['risk_score']:.2f}")
            if "opportunity_score" in result:
                extras.append(f"opportunity {result['opportunity_score']:.2f}")
            if extras:
                print(f"          {DIM}  ({', '.join(extras)}){RESET}")
        print()

    # ─── Phase 2: Sequential — writer synthesizes ─────────────────────────

    section("PHASE 2  ·  Report Writing  (sequential, uses Phase 1 output)")

    research_data = researcher_result.get("result", {}) if researcher_result["status"] == "success" else {}
    analysis_data = analyst_result.get("result", {}) if analyst_result["status"] == "success" else {}

    # Synthesize research + analysis into a human-readable briefing
    # instead of dumping raw JSON to the writer agent.
    from zyndai_agent.orchestration.coordinator import _format_result_for_briefing

    briefing_parts = []
    if research_data:
        briefing_parts.append(f"[Research]\n{_format_result_for_briefing(research_data)}")
    if analysis_data:
        briefing_parts.append(f"[Analysis]\n{_format_result_for_briefing(analysis_data)}")
    synthesized_briefing = "\n\n".join(briefing_parts) or "No data available."

    progress("Sending synthesized briefing to writer agent")
    p2_start = time.time()

    writer_result = await call_worker(
        coordinator, session, tracker, "writer",
        worker_urls["writer"],
        f"Write an executive summary about '{topic}'. "
        f"Here is the synthesized research and analysis:\n\n{synthesized_briefing}",
        cost_usd=0.002,
    )

    p2_ms = (time.time() - p2_start) * 1000
    icon = CHECK if writer_result["status"] == "success" else CROSS
    progress_done(f"Writer responded in {fmt_ms(p2_ms)}\n")
    status(icon, "writer", fmt_ms(writer_result.get("duration_ms", 0)))

    builtins.print = _orig_print

    total_ms = (time.time() - t0) * 1000
    summary_obj = tracker.summary()

    return {
        "researcher": researcher_result,
        "analyst": analyst_result,
        "writer": writer_result,
        "p1_ms": p1_ms,
        "p2_ms": p2_ms,
        "total_ms": total_ms,
        "task_summary": summary_obj,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    topic = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "AI agents that discover and pay each other using micropayments"

    banner("ZyndAI Orchestration Demo")
    print()
    kvline("Topic:", f'"{topic}"')
    kvline("AI Backend:", AI_MODE)
    kvline("Agents:", "4 (1 coordinator + 3 specialist workers)")
    kvline("Protocol:", "HTTP webhooks + Ed25519 signatures + x402")
    kvline("Payment:", "x402 micropayments on Base Sepolia")

    tmpdir = tempfile.mkdtemp(prefix="zyndai_demo_")

    # ─── Boot agents ──────────────────────────────────────────────────────

    section("STARTING AGENTS")

    agents = {}
    agent_defs = [
        ("coordinator", "Orchestrates multi-agent pipelines",    ["orchestration"],           "orchestration", 7200),
        ("researcher",  "Finds information and key facts",       ["research", "web-search"],  "research",      7201),
        ("analyst",     "Analyzes trends and identifies risks",  ["analysis", "data"],        "analysis",      7202),
        ("writer",      "Writes executive summaries",            ["writing", "summarization"],"content",       7203),
    ]

    for name, desc, skills, cat, port in agent_defs:
        agents[name] = create_agent(tmpdir, name, desc, skills, cat, port)

    # Register handlers
    agents["researcher"].register_handler(researcher_handler(agents["researcher"]))
    agents["analyst"].register_handler(analyst_handler(agents["analyst"]))
    agents["writer"].register_handler(writer_handler(agents["writer"]))

    # Wait for health
    for name, agent in agents.items():
        url = agent_base_url(agent)
        wait_healthy(url, name)
        aid = agent.agent_id[:16]
        pk = agent.keypair.public_key_b64[:12] if agent.keypair else "none"
        status(CHECK, f"{name:14s} {CYAN}{url:28s}{RESET}  {DIM}id={aid}…  key={pk}…{RESET}")

    # Discover workers via registry search (the real agent discovery flow)
    section("DISCOVERING AGENTS VIA REGISTRY")

    from zyndai_agent import dns_registry

    worker_urls = {}
    coordinator = agents["coordinator"]
    for worker_name, search_keyword in [("researcher", "research"), ("analyst", "analysis"), ("writer", "writing")]:
        try:
            found = coordinator.search_agents(keyword=search_keyword, limit=5)
            match = next((a for a in found if a.get("name") == worker_name), None)
            if match:
                # Search results may not include agent_url — fetch full record
                agent_id = match["agent_id"]
                full_record = dns_registry.get_agent(REGISTRY_URL, agent_id)
                url = full_record.get("agent_url", "") if full_record else ""
                if url:
                    worker_urls[worker_name] = url
                    score = match.get("score", 0)
                    status(CHECK, f'search("{search_keyword}") {ARROW} "{match["name"]}" at {url}  {DIM}score={score:.2f}{RESET}')
                else:
                    worker_urls[worker_name] = agent_base_url(agents[worker_name])
                    status(CHECK, f'search("{search_keyword}") {ARROW} "{match["name"]}" found (resolving URL locally)')
            else:
                worker_urls[worker_name] = agent_base_url(agents[worker_name])
                status(CLOCK, f'search("{search_keyword}") {ARROW} not in results yet, using direct URL')
        except Exception as e:
            worker_urls[worker_name] = agent_base_url(agents[worker_name])
            status(CLOCK, f'search("{search_keyword}") {ARROW} fallback ({e})')

    # ─── Run pipeline ─────────────────────────────────────────────────────

    results = asyncio.run(run_pipeline(agents["coordinator"], worker_urls, topic))

    # ─── Executive Summary ────────────────────────────────────────────────

    section("EXECUTIVE SUMMARY")

    if results["writer"]["status"] == "success":
        summary_text = results["writer"]["result"].get("summary", "No summary generated.")
        wrap_text(summary_text, width=64, indent="    ")
    else:
        print(f"    {CROSS} Writer failed: {results['writer'].get('error')}")

    # ─── Pipeline Metrics ─────────────────────────────────────────────────

    section("PIPELINE METRICS")

    ts = results["task_summary"]
    kvline("Total tasks:", str(ts["total"]))
    kvline("Completed:", str(ts["by_status"].get("completed", 0)))
    kvline("Failed:", str(ts["by_status"].get("failed", 0)))
    kvline("Total cost:", f'${ts["total_cost_usd"]:.4f} USDC')
    print()
    kvline("Phase 1 (parallel):", fmt_ms(results["p1_ms"]))
    kvline("Phase 2 (sequential):", fmt_ms(results["p2_ms"]))
    kvline("Total pipeline:", f'{fmt_ms(results["total_ms"])}')

    seq_time = (results["researcher"].get("duration_ms", 0) or 0) + \
               (results["analyst"].get("duration_ms", 0) or 0) + \
               (results["writer"].get("duration_ms", 0) or 0)
    if seq_time > 0:
        speedup = seq_time / results["total_ms"]
        kvline("Parallelism gain:", f'{speedup:.1f}x faster than sequential')

    # ─── Signature Verification Proof ─────────────────────────────────────

    section("SECURITY  ·  Ed25519 Signature Verification")

    coord = agents["coordinator"]
    msg = InvokeMessage(
        sender_id=coord.agent_id,
        sender_public_key=coord.keypair.public_key_string,
        capability="demo",
        payload={"proof": "this message is signed"},
    )
    msg.signature = sign_message(msg, coord.keypair.private_key)
    verified = verify_message(msg, coord.keypair.public_key_b64)
    status(CHECK if verified else CROSS, f"Coordinator signature verified: {verified}")

    tampered = InvokeMessage(**msg.model_dump())
    tampered.payload = {"proof": "TAMPERED"}
    tampered_result = verify_message(tampered, coord.keypair.public_key_b64)
    status(CHECK if not tampered_result else CROSS, f"Tampered message rejected: {not tampered_result}")

    # ─── Session State ────────────────────────────────────────────────────

    section("SESSION STATE  ·  Per-Agent Conversation Tracking")

    for name, agent in agents.items():
        sessions = agent.active_sessions
        total_msgs = sum(len(s.messages) for s in sessions)
        status("📋", f"{name:14s} {len(sessions)} session(s), {total_msgs} message(s)")

    # ─── Done ─────────────────────────────────────────────────────────────

    print()
    for agent in agents.values():
        agent.stop_heartbeat()
    banner("Demo Complete")
    print(f"\n    Run with a custom topic:")
    print(f'    {DIM}uv run python examples/orchestration_demo.py "your topic here"{RESET}\n')


if __name__ == "__main__":
    main()
