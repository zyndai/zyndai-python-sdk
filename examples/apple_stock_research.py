#!/usr/bin/env python3
"""
ZyndAI Market Research Demo — Apple Stock Analysis

3 specialist agents + 1 coordinator analyze AAPL:
  - Data Collector: fetches price history, financials, key metrics
  - Sentiment Analyst: analyzes market sentiment, news, social signals
  - Strategy Advisor: produces buy/hold/sell recommendation with rationale

Run:
    cd zyndai-agent
    uv run python examples/apple_stock_research.py
"""

import asyncio
import json
import logging
import os
import sys
import time
import tempfile
import textwrap
import io
import builtins
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

logging.getLogger("werkzeug").setLevel(logging.ERROR)
logging.getLogger("WebhookAgentCommunication").setLevel(logging.ERROR)
logging.getLogger("urllib3").setLevel(logging.ERROR)

_real_print = builtins.print


class _Quiet:
    def __enter__(self):
        self._o, self._e = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
    def __exit__(self, *a):
        sys.stdout, sys.stderr = self._o, self._e


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

_llm = None
try:
    import openai
    if os.getenv("OPENAI_API_KEY"):
        _llm = openai.OpenAI()
except ImportError:
    pass

AI_MODE = "GPT-4o-mini" if _llm else "Built-in Logic"


# ═══════════════════════════════════════════════════════════════════════════════
#  Logger
# ═══════════════════════════════════════════════════════════════════════════════

DIM = "\033[2m"
BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RED = "\033[31m"
MAGENTA = "\033[35m"
WHITE = "\033[37m"
RESET = "\033[0m"

COLORS = {
    "SYSTEM":      f"{BOLD}{CYAN}",
    "COORDINATOR": f"{BOLD}{MAGENTA}",
    "DATA":        f"{BOLD}{GREEN}",
    "SENTIMENT":   f"{BOLD}{YELLOW}",
    "STRATEGY":    f"{BOLD}{WHITE}",
}

_t0 = time.time()


def log(agent, msg, detail=""):
    elapsed = time.time() - _t0
    c = COLORS.get(agent.upper(), DIM)
    _real_print(f"  {DIM}{elapsed:6.1f}s{RESET}  {c}{agent:14s}{RESET}  {msg}")
    if detail:
        for line in detail.split("\n"):
            _real_print(f"  {DIM}{' ':6s}{RESET}  {' ':14s}  {DIM}{line}{RESET}")
    time.sleep(0.04)


def divider(label=""):
    _real_print()
    if label:
        _real_print(f"  {BOLD}{'─' * 3} {label} {'─' * max(0, 55 - len(label))}{RESET}")
    _real_print()


# ═══════════════════════════════════════════════════════════════════════════════
#  LLM
# ═══════════════════════════════════════════════════════════════════════════════

def ask_llm(agent_name, system, prompt, max_tokens=400):
    if _llm:
        log(agent_name, "Calling GPT-4o-mini...")
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

def base_url(agent):
    return agent.webhook_url.replace("/webhook", "")


def boot_agent(tmpdir, name, desc, skills, category, port):
    log("SYSTEM", f"Booting {name}...", f"port={port}  skills={skills}")
    d = os.path.join(tmpdir, name)
    os.makedirs(d, exist_ok=True)
    with _Quiet():
        agent = ZyndAIAgent(AgentConfig(
            name=name, description=desc,
            capabilities={"skills": skills},
            category=category, summary=desc[:200], tags=skills,
            webhook_port=port, config_dir=d,
            registry_url=REGISTRY_URL,
        ))
    url = base_url(agent)
    log("SYSTEM", f"{GREEN}✓{RESET} {name} online at {CYAN}{url}{RESET}",
        f"id={agent.agent_id}  key={agent.keypair.public_key_b64[:16]}...")
    dns_registry.update_agent(REGISTRY_URL, agent.agent_id, agent.keypair, {"agent_url": url})
    return agent


def _extract(msg):
    try:
        t = parse_message(msg.to_dict())
        if hasattr(t, "payload") and isinstance(t.payload, dict):
            return t.payload.get("content") or t.payload.get("task") or msg.content or ""
    except Exception:
        pass
    return msg.content or ""


# ═══════════════════════════════════════════════════════════════════════════════
#  Worker Handlers
# ═══════════════════════════════════════════════════════════════════════════════

def data_handler(agent):
    def handler(msg, topic, session):
        task = _extract(msg)
        log("DATA", f"Received: \"{task[:70]}...\"")

        if _llm:
            answer = ask_llm("DATA",
                "You are a financial data analyst specializing in stock market data. "
                "Return ONLY valid JSON (no markdown) with these keys: "
                "ticker (str), current_price (float), price_52w_high (float), price_52w_low (float), "
                "pe_ratio (float), market_cap_billions (float), revenue_growth_yoy (str), "
                "eps (float), dividend_yield (str), key_metrics (list of 3 strings).",
                f"Provide current financial data and key metrics for: {task}",
            )
            answer = answer.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            try:
                result = json.loads(answer)
            except json.JSONDecodeError:
                result = _data_fallback()
        else:
            log("DATA", "Fetching data with built-in logic...")
            time.sleep(0.8)
            result = _data_fallback()

        log("DATA", f"{GREEN}✓{RESET} Data ready — {list(result.keys())}")
        agent.set_response(msg.message_id, json.dumps(result))
    return handler


def _data_fallback():
    return {
        "ticker": "AAPL",
        "current_price": 228.50,
        "price_52w_high": 260.10,
        "price_52w_low": 164.08,
        "pe_ratio": 37.8,
        "market_cap_billions": 3480,
        "revenue_growth_yoy": "+4.3%",
        "eps": 6.04,
        "dividend_yield": "0.44%",
        "key_metrics": [
            "Services revenue hit $26.3B in Q1 2025, up 14% YoY",
            "iPhone revenue declined 1% but ASP increased",
            "Gross margin expanded to 46.9%, highest in a decade",
        ],
    }


def sentiment_handler(agent):
    def handler(msg, topic, session):
        task = _extract(msg)
        log("SENTIMENT", f"Received: \"{task[:70]}...\"")

        if _llm:
            answer = ask_llm("SENTIMENT",
                "You are a market sentiment analyst. You analyze news, social media, analyst ratings, and institutional flow. "
                "Return ONLY valid JSON (no markdown) with these keys: "
                "overall_sentiment (str: bullish/neutral/bearish), confidence (float 0-1), "
                "analyst_consensus (str), price_target_avg (float), "
                "bull_signals (list of 3 strings), bear_signals (list of 2 strings), "
                "recent_news (list of 2 strings).",
                f"Analyze current market sentiment for: {task}",
            )
            answer = answer.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            try:
                result = json.loads(answer)
            except json.JSONDecodeError:
                result = _sentiment_fallback()
        else:
            log("SENTIMENT", "Analyzing sentiment with built-in logic...")
            time.sleep(0.6)
            result = _sentiment_fallback()

        log("SENTIMENT", f"{GREEN}✓{RESET} Sentiment ready — {result.get('overall_sentiment', 'unknown')}")
        agent.set_response(msg.message_id, json.dumps(result))
    return handler


def _sentiment_fallback():
    return {
        "overall_sentiment": "bullish",
        "confidence": 0.72,
        "analyst_consensus": "Overweight",
        "price_target_avg": 252.40,
        "bull_signals": [
            "Services segment growing 14% YoY — becoming a recurring revenue machine",
            "Apple Intelligence rollout driving upgrade cycle expectations",
            "Share buyback program continues at $110B+ annually",
        ],
        "bear_signals": [
            "China revenue down 11% amid local competition from Huawei",
            "Valuation premium at 37.8x PE — priced for perfection",
        ],
        "recent_news": [
            "Apple announces AI partnership with OpenAI for on-device models",
            "EU Digital Markets Act forcing App Store fee restructuring",
        ],
    }


def strategy_handler(agent):
    def handler(msg, topic, session):
        task = _extract(msg)
        log("STRATEGY", f"Received briefing ({len(task)} chars)")

        if _llm:
            answer = ask_llm("STRATEGY",
                "You are a senior investment strategist. Given research data and sentiment analysis, "
                "produce an actionable recommendation. "
                "Return ONLY valid JSON (no markdown) with these keys: "
                "recommendation (str: BUY/HOLD/SELL), confidence (float 0-1), "
                "target_price (float), time_horizon (str), "
                "rationale (str — 3-4 sentences), "
                "risks (list of 2 strings), catalysts (list of 2 strings).",
                f"Based on the following research and sentiment data, provide an investment recommendation:\n\n{task}",
                max_tokens=500,
            )
            answer = answer.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            try:
                result = json.loads(answer)
            except json.JSONDecodeError:
                result = _strategy_fallback()
        else:
            log("STRATEGY", "Building recommendation with built-in logic...")
            time.sleep(0.5)
            result = _strategy_fallback()

        log("STRATEGY", f"{GREEN}✓{RESET} Recommendation: {BOLD}{result.get('recommendation', '?')}{RESET}")
        agent.set_response(msg.message_id, json.dumps(result))
    return handler


def _strategy_fallback():
    return {
        "recommendation": "HOLD",
        "confidence": 0.68,
        "target_price": 245.00,
        "time_horizon": "6-12 months",
        "rationale": (
            "Apple's fundamentals remain strong with Services growing at 14% and gross margins "
            "at decade highs. However, the current PE of 37.8x leaves little room for error. "
            "The Apple Intelligence rollout could drive an upgrade supercycle, but China headwinds "
            "and regulatory pressure in the EU create near-term uncertainty. "
            "Wait for a pullback to the $210-215 range for a better entry point."
        ),
        "risks": [
            "China revenue deterioration accelerates if Huawei gains more share",
            "EU App Store regulation could reduce Services margin by 200-300bps",
        ],
        "catalysts": [
            "iPhone 17 with Apple Intelligence driving record upgrade cycle",
            "Services revenue crossing $30B/quarter milestone",
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  Orchestration
# ═══════════════════════════════════════════════════════════════════════════════

async def call_worker(coordinator, session, tracker, name, label, url, task_desc, cost=0.001):
    log("COORDINATOR", f"Building InvokeMessage for {label}")
    msg = InvokeMessage(
        conversation_id=session.conversation_id,
        sender_id=coordinator.agent_id,
        sender_public_key=coordinator.keypair.public_key_string,
        capability=name, payload={"task": task_desc, "content": task_desc},
        timeout_seconds=30,
    )
    log("COORDINATOR", f"Signing with Ed25519...")
    msg.signature = sign_message(msg, coordinator.keypair.private_key)
    log("COORDINATOR", f"POST {CYAN}{url}/webhook/sync{RESET} → {label}")

    task = tracker.create_task(description=f"[{name}]", assigned_to=url)
    task.mark_running()

    try:
        resp = await asyncio.to_thread(
            coordinator.x402_processor.session.post,
            f"{url}/webhook/sync", json=msg.model_dump(mode="json"), timeout=30,
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
        return {"status": "error", "agent": name, "error": str(e)}


async def run_pipeline(coordinator, worker_urls):
    session = AgentSession(conversation_id=generate_id())
    tracker = TaskTracker()

    # Suppress Flask noise
    _orig = builtins.print
    builtins.print = lambda *a, **k: (_orig(*a, **k) if "Incoming" not in " ".join(str(x) for x in a) else None)

    # ─── Phase 1: Data + Sentiment in parallel ───────────────────────────

    divider("PHASE 1: DATA COLLECTION + SENTIMENT ANALYSIS (parallel)")

    log("COORDINATOR", "Analyzing AAPL — breaking into 2 parallel tasks...")
    log("COORDINATOR", f"  → DATA COLLECTOR: fetch financials, price, metrics")
    log("COORDINATOR", f"  → SENTIMENT ANALYST: news, analyst ratings, social signals")
    log("COORDINATOR", "Dispatching both simultaneously...")

    p1_start = time.time()
    data_result, sentiment_result = await asyncio.gather(
        call_worker(coordinator, session, tracker, "data-collector", "DATA",
                    worker_urls["data-collector"],
                    "Fetch current financial data, price history, and key metrics for Apple Inc (AAPL)"),
        call_worker(coordinator, session, tracker, "sentiment-analyst", "SENTIMENT",
                    worker_urls["sentiment-analyst"],
                    "Analyze current market sentiment, analyst ratings, news, and social signals for Apple Inc (AAPL)"),
    )
    p1_ms = (time.time() - p1_start) * 1000
    log("COORDINATOR", f"Phase 1 complete in {p1_ms:.0f}ms (parallel)")

    # Show results
    if data_result["status"] == "success":
        r = data_result["result"]
        log("COORDINATOR", "Reading DATA COLLECTOR results:")
        log("COORDINATOR", f"  Price: ${r.get('current_price', '?')}  |  PE: {r.get('pe_ratio', '?')}  |  MCap: ${r.get('market_cap_billions', '?')}B")
        log("COORDINATOR", f"  52W Range: ${r.get('price_52w_low', '?')} - ${r.get('price_52w_high', '?')}")
        for m in r.get("key_metrics", []):
            log("COORDINATOR", f"  • {m}")

    if sentiment_result["status"] == "success":
        r = sentiment_result["result"]
        log("COORDINATOR", "Reading SENTIMENT ANALYST results:")
        log("COORDINATOR", f"  Sentiment: {BOLD}{r.get('overall_sentiment', '?').upper()}{RESET}  |  Confidence: {r.get('confidence', '?')}")
        log("COORDINATOR", f"  Analyst consensus: {r.get('analyst_consensus', '?')}  |  Target: ${r.get('price_target_avg', '?')}")

    # ─── Phase 2: Strategy recommendation ─────────────────────────────────

    divider("PHASE 2: INVESTMENT STRATEGY (sequential, uses Phase 1)")

    log("COORDINATOR", "Synthesizing data + sentiment into briefing for strategist...")

    data_d = data_result.get("result", {}) if data_result["status"] == "success" else {}
    sent_d = sentiment_result.get("result", {}) if sentiment_result["status"] == "success" else {}

    parts = []
    if data_d:
        parts.append(f"[Financial Data]\n{_format_result_for_briefing(data_d)}")
    if sent_d:
        parts.append(f"[Sentiment Analysis]\n{_format_result_for_briefing(sent_d)}")
    briefing = "\n\n".join(parts) or "No data."

    log("COORDINATOR", f"Briefing ready ({len(briefing)} chars) — sending to STRATEGY ADVISOR")

    p2_start = time.time()
    strategy_result = await call_worker(
        coordinator, session, tracker, "strategy-advisor", "STRATEGY",
        worker_urls["strategy-advisor"],
        f"Based on the following financial data and sentiment analysis for Apple Inc (AAPL), "
        f"provide an investment recommendation:\n\n{briefing}",
        cost=0.002,
    )
    p2_ms = (time.time() - p2_start) * 1000

    builtins.print = _orig

    total_ms = p1_ms + p2_ms

    return {
        "data": data_result,
        "sentiment": sentiment_result,
        "strategy": strategy_result,
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
    _real_print(f"  {BOLD}  AAPL Market Research — ZyndAI Multi-Agent Pipeline{RESET}")
    _real_print(f"  {BOLD}{'═' * 64}{RESET}")
    _real_print()

    log("SYSTEM", f"Target: {BOLD}Apple Inc (AAPL){RESET}")
    log("SYSTEM", f"AI Backend: {AI_MODE}")
    log("SYSTEM", f"Registry: {REGISTRY_URL}")
    log("SYSTEM", f"Agents: 4 (coordinator + data collector + sentiment analyst + strategy advisor)")

    tmpdir = tempfile.mkdtemp(prefix="zyndai_aapl_")

    divider("BOOTING AGENTS")

    agents = {}

    agents["aapl-coordinator"] = boot_agent(tmpdir, "aapl-coordinator",
        "Orchestrates market research pipeline for stock analysis",
        ["orchestration", "market-research"], "orchestration", 7300)

    agents["data-collector"] = boot_agent(tmpdir, "data-collector",
        "Collects financial data, price history, and key metrics for stocks",
        ["financial-data", "stock-metrics", "fundamentals"], "finance", 7301)

    agents["sentiment-analyst"] = boot_agent(tmpdir, "sentiment-analyst",
        "Analyzes market sentiment from news, social media, and analyst ratings",
        ["sentiment-analysis", "market-sentiment", "news"], "analysis", 7302)

    agents["strategy-advisor"] = boot_agent(tmpdir, "strategy-advisor",
        "Produces investment recommendations based on data and sentiment",
        ["investment-strategy", "buy-sell", "portfolio"], "advisory", 7303)

    agents["data-collector"].register_handler(data_handler(agents["data-collector"]))
    agents["sentiment-analyst"].register_handler(sentiment_handler(agents["sentiment-analyst"]))
    agents["strategy-advisor"].register_handler(strategy_handler(agents["strategy-advisor"]))

    worker_urls = {n: base_url(a) for n, a in agents.items() if n != "aapl-coordinator"}

    # Health check
    for name, agent in agents.items():
        url = base_url(agent)
        for _ in range(30):
            try:
                if requests.get(f"{url}/health", timeout=2).status_code == 200:
                    break
            except requests.ConnectionError:
                pass
            time.sleep(0.2)

    divider("DISCOVERING AGENTS VIA REGISTRY")

    log("COORDINATOR", f"Searching {CYAN}{REGISTRY_URL}{RESET} for specialist agents...")

    coordinator = agents["aapl-coordinator"]
    for wname, keyword in [("data-collector", "financial-data"), ("sentiment-analyst", "sentiment"), ("strategy-advisor", "investment-strategy")]:
        try:
            found = coordinator.search_agents(keyword=keyword, limit=5)
            match = next((a for a in found if a.get("name") == wname), None)
            if match:
                full = dns_registry.get_agent(REGISTRY_URL, match["agent_id"])
                url = (full or {}).get("agent_url", "") or base_url(agents[wname])
                worker_urls[wname] = url
                score = match.get("score", 0)
                log("COORDINATOR", f'{GREEN}✓{RESET} search("{keyword}") → {BOLD}{match["name"]}{RESET}  score={score:.2f}')
            else:
                log("COORDINATOR", f'{YELLOW}⏱{RESET} search("{keyword}") → resolving locally')
        except Exception:
            log("COORDINATOR", f'{YELLOW}⏱{RESET} search("{keyword}") → fallback')

    # Run pipeline
    results = asyncio.run(run_pipeline(coordinator, worker_urls))

    # ─── Final Output ─────────────────────────────────────────────────────

    divider("INVESTMENT RECOMMENDATION")

    if results["strategy"]["status"] == "success":
        s = results["strategy"]["result"]
        rec = s.get("recommendation", "?")
        rec_color = GREEN if rec == "BUY" else (YELLOW if rec == "HOLD" else RED)

        _real_print(f"    {BOLD}Ticker:{RESET}          AAPL")
        _real_print(f"    {BOLD}Recommendation:{RESET}  {rec_color}{BOLD}{rec}{RESET}")
        _real_print(f"    {BOLD}Confidence:{RESET}      {s.get('confidence', '?')}")
        _real_print(f"    {BOLD}Target Price:{RESET}    ${s.get('target_price', '?')}")
        _real_print(f"    {BOLD}Time Horizon:{RESET}    {s.get('time_horizon', '?')}")
        _real_print()
        _real_print(f"    {BOLD}Rationale:{RESET}")
        for line in textwrap.wrap(s.get("rationale", ""), 60):
            _real_print(f"      {line}")
        _real_print()
        _real_print(f"    {BOLD}Catalysts:{RESET}")
        for c in s.get("catalysts", []):
            _real_print(f"      {GREEN}▲{RESET} {c}")
        _real_print()
        _real_print(f"    {BOLD}Risks:{RESET}")
        for r in s.get("risks", []):
            _real_print(f"      {RED}▼{RESET} {r}")
    else:
        _real_print(f"    {RED}Strategy agent failed: {results['strategy'].get('error')}{RESET}")

    divider("PIPELINE METRICS")

    ts = results["task_summary"]
    log("SYSTEM", f"Tasks: {ts['total']} completed, {ts['by_status'].get('failed', 0)} failed")
    log("SYSTEM", f"Cost: ${ts['total_cost_usd']:.4f} USDC")
    log("SYSTEM", f"Phase 1 (data + sentiment, parallel): {results['p1_ms']:.0f}ms")
    log("SYSTEM", f"Phase 2 (strategy, sequential): {results['p2_ms']:.0f}ms")
    log("SYSTEM", f"Total pipeline: {results['total_ms']:.0f}ms")

    d_ms = results["data"].get("duration_ms", 0) or 0
    s_ms = results["sentiment"].get("duration_ms", 0) or 0
    st_ms = results["strategy"].get("duration_ms", 0) or 0
    seq = d_ms + s_ms + st_ms
    if seq > 0 and results["total_ms"] > 0:
        log("SYSTEM", f"Parallelism: {seq / results['total_ms']:.1f}x faster than sequential")

    _real_print()
    for a in agents.values():
        a.stop_heartbeat()

    _real_print(f"  {BOLD}{'═' * 64}{RESET}")
    _real_print(f"  {BOLD}  Analysis Complete{RESET}")
    _real_print(f"  {BOLD}{'═' * 64}{RESET}")
    _real_print()


if __name__ == "__main__":
    main()
