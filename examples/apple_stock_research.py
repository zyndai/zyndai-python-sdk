#!/usr/bin/env python3
"""
ZyndAI Market Research Demo — Apple Stock Analysis (Real Data)

3 specialist agents + 1 coordinator analyze AAPL using real public APIs:
  - Data Collector: Yahoo Finance — live price, financials, margins, history
  - Sentiment Analyst: Yahoo Finance news + GPT-4o-mini sentiment scoring
  - Strategy Advisor: GPT-4o-mini recommendation based on real data

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
from dotenv import load_dotenv

load_dotenv()

logging.getLogger("werkzeug").setLevel(logging.ERROR)
logging.getLogger("WebhookAgentCommunication").setLevel(logging.ERROR)
logging.getLogger("urllib3").setLevel(logging.ERROR)
logging.getLogger("yfinance").setLevel(logging.ERROR)
logging.getLogger("peewee").setLevel(logging.ERROR)

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
TICKER = os.getenv("TICKER", "AAPL")

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


def ask_llm(agent_name, system, prompt, max_tokens=400):
    if not _llm:
        return ""
    log(agent_name, "Calling GPT-4o-mini for analysis...")
    resp = _llm.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        max_tokens=max_tokens, temperature=0.4,
    )
    answer = resp.choices[0].message.content.strip()
    tokens = resp.usage.total_tokens if resp.usage else 0
    log(agent_name, f"LLM responded ({tokens} tokens)")
    return answer


# ═══════════════════════════════════════════════════════════════════════════════
#  Agent Factory
# ═══════════════════════════════════════════════════════════════════════════════

def base_url(agent):
    return agent.webhook_url.replace("/webhook", "")


def boot_agent(tmpdir, name, desc, skills, category, port):
    log("SYSTEM", f"Booting {name}...", f"port={port}")
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
        f"id={agent.agent_id[:24]}...")
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
#  Data Collector — Yahoo Finance (real data, no LLM)
# ═══════════════════════════════════════════════════════════════════════════════

def data_handler(agent):
    def handler(msg, topic, session):
        task = _extract(msg)
        log("DATA", f"Received task: \"{task[:60]}...\"")
        log("DATA", f"Fetching live data from Yahoo Finance for {TICKER}...")

        import yfinance as yf
        t = yf.Ticker(TICKER)
        info = t.info

        current = info.get("currentPrice", 0)
        prev_close = info.get("previousClose", 0)
        change_pct = ((current - prev_close) / prev_close * 100) if prev_close else 0

        result = {
            "ticker": TICKER,
            "company": info.get("shortName", TICKER),
            "current_price": current,
            "previous_close": prev_close,
            "change_pct": round(change_pct, 2),
            "price_52w_high": info.get("fiftyTwoWeekHigh", 0),
            "price_52w_low": info.get("fiftyTwoWeekLow", 0),
            "pe_ratio": round(info.get("trailingPE", 0), 2),
            "forward_pe": round(info.get("forwardPE", 0), 2),
            "market_cap_billions": round(info.get("marketCap", 0) / 1e9, 1),
            "revenue_growth_yoy": f"{info.get('revenueGrowth', 0) * 100:.1f}%",
            "eps_ttm": info.get("trailingEps", 0),
            "forward_eps": info.get("forwardEps", 0),
            "dividend_yield": f"{(info.get('dividendYield', 0) or 0) * 100:.2f}%",
            "gross_margin": f"{(info.get('grossMargins', 0) or 0) * 100:.1f}%",
            "operating_margin": f"{(info.get('operatingMargins', 0) or 0) * 100:.1f}%",
            "profit_margin": f"{(info.get('profitMargins', 0) or 0) * 100:.1f}%",
            "return_on_equity": f"{(info.get('returnOnEquity', 0) or 0) * 100:.1f}%",
            "debt_to_equity": info.get("debtToEquity", 0),
            "free_cash_flow_billions": round(info.get("freeCashflow", 0) / 1e9, 1),
            "beta": info.get("beta", 0),
            "avg_volume_millions": round(info.get("averageVolume", 0) / 1e6, 1),
            "sector": info.get("sector", ""),
            "industry": info.get("industry", ""),
        }

        log("DATA", f"{GREEN}✓{RESET} Live data: ${current} | PE {result['pe_ratio']} | MCap ${result['market_cap_billions']}B")
        agent.set_response(msg.message_id, json.dumps(result))
    return handler


# ═══════════════════════════════════════════════════════════════════════════════
#  Sentiment Analyst — Yahoo Finance News + GPT-4o-mini Analysis
# ═══════════════════════════════════════════════════════════════════════════════

def sentiment_handler(agent):
    def handler(msg, topic, session):
        task = _extract(msg)
        log("SENTIMENT", f"Received task: \"{task[:60]}...\"")
        log("SENTIMENT", f"Fetching news and analyst data from Yahoo Finance...")

        import yfinance as yf
        t = yf.Ticker(TICKER)
        info = t.info

        # Real analyst recommendations
        analyst_target = info.get("targetMeanPrice", 0)
        analyst_high = info.get("targetHighPrice", 0)
        analyst_low = info.get("targetLowPrice", 0)
        analyst_count = info.get("numberOfAnalystOpinions", 0)
        rec_key = info.get("recommendationKey", "none")

        # Real recommendation breakdown
        recs = t.recommendations
        rec_breakdown = {}
        if recs is not None and len(recs) > 0:
            latest = recs.iloc[0]
            rec_breakdown = {
                "strong_buy": int(latest.get("strongBuy", 0)),
                "buy": int(latest.get("buy", 0)),
                "hold": int(latest.get("hold", 0)),
                "sell": int(latest.get("sell", 0)),
                "strong_sell": int(latest.get("strongSell", 0)),
            }

        # Real news headlines
        headlines = []
        news = t.news or []
        for n in news[:8]:
            content = n.get("content", {})
            if isinstance(content, dict):
                title = content.get("title", "")
                if title:
                    headlines.append(title)

        log("SENTIMENT", f"Got {len(headlines)} headlines, {analyst_count} analyst opinions")

        # Use GPT-4o-mini to score sentiment from real headlines
        sentiment_score = "neutral"
        sentiment_confidence = 0.5
        bull_signals = []
        bear_signals = []

        if _llm and headlines:
            analysis = ask_llm("SENTIMENT",
                "You are a financial sentiment analyst. Given real news headlines about a stock, "
                "return ONLY valid JSON with: overall_sentiment (bullish/neutral/bearish), "
                "confidence (float 0-1), bull_signals (list of 2-3 strings), bear_signals (list of 1-2 strings). "
                "Base your analysis strictly on the headlines provided.",
                f"Analyze sentiment for {TICKER} based on these recent headlines:\n" +
                "\n".join(f"- {h}" for h in headlines),
            )
            analysis = analysis.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            try:
                parsed = json.loads(analysis)
                sentiment_score = parsed.get("overall_sentiment", "neutral")
                sentiment_confidence = parsed.get("confidence", 0.5)
                bull_signals = parsed.get("bull_signals", [])
                bear_signals = parsed.get("bear_signals", [])
            except json.JSONDecodeError:
                pass
        elif headlines:
            sentiment_score = "neutral"
            sentiment_confidence = 0.5
            bull_signals = [headlines[0]] if headlines else []
            bear_signals = [headlines[-1]] if len(headlines) > 1 else []

        result = {
            "overall_sentiment": sentiment_score,
            "confidence": sentiment_confidence,
            "analyst_consensus": rec_key,
            "analyst_count": analyst_count,
            "price_target_mean": analyst_target,
            "price_target_high": analyst_high,
            "price_target_low": analyst_low,
            "recommendation_breakdown": rec_breakdown,
            "recent_headlines": headlines[:5],
            "bull_signals": bull_signals,
            "bear_signals": bear_signals,
        }

        log("SENTIMENT", f"{GREEN}✓{RESET} Sentiment: {BOLD}{sentiment_score.upper()}{RESET} | "
            f"Analyst consensus: {rec_key} | Target: ${analyst_target}")
        agent.set_response(msg.message_id, json.dumps(result))
    return handler


# ═══════════════════════════════════════════════════════════════════════════════
#  Strategy Advisor — GPT-4o-mini on Real Data
# ═══════════════════════════════════════════════════════════════════════════════

def strategy_handler(agent):
    def handler(msg, topic, session):
        task = _extract(msg)
        log("STRATEGY", f"Received briefing ({len(task)} chars)")

        if _llm:
            answer = ask_llm("STRATEGY",
                "You are a senior investment strategist. You receive REAL financial data "
                "and sentiment analysis. Produce an actionable recommendation. "
                "Return ONLY valid JSON with: recommendation (BUY/HOLD/SELL), confidence (float 0-1), "
                "target_price (float), time_horizon (str), "
                "rationale (str — 4-5 sentences referencing the actual numbers), "
                "risks (list of 2-3 strings), catalysts (list of 2-3 strings).",
                f"Based on this REAL market data and sentiment for {TICKER}:\n\n{task}",
                max_tokens=600,
            )
            answer = answer.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            try:
                result = json.loads(answer)
            except json.JSONDecodeError:
                result = {"recommendation": "HOLD", "confidence": 0.5,
                          "rationale": answer, "risks": [], "catalysts": []}
        else:
            result = {
                "recommendation": "HOLD", "confidence": 0.6,
                "target_price": 260.0, "time_horizon": "6-12 months",
                "rationale": "Insufficient AI backend for full analysis. Use with OPENAI_API_KEY for real recommendations.",
                "risks": ["No LLM analysis available"], "catalysts": ["Set OPENAI_API_KEY"],
            }

        rec = result.get("recommendation", "?")
        log("STRATEGY", f"{GREEN}✓{RESET} Recommendation: {BOLD}{rec}{RESET}")
        agent.set_response(msg.message_id, json.dumps(result))
    return handler


# ═══════════════════════════════════════════════════════════════════════════════
#  Orchestration
# ═══════════════════════════════════════════════════════════════════════════════

async def call_worker(coordinator, session, tracker, name, label, url, task_desc, cost=0.001):
    log("COORDINATOR", f"Signing + dispatching to {label}...")
    msg = InvokeMessage(
        conversation_id=session.conversation_id,
        sender_id=coordinator.agent_id,
        sender_public_key=coordinator.keypair.public_key_string,
        capability=name, payload={"task": task_desc, "content": task_desc},
        timeout_seconds=30,
    )
    msg.signature = sign_message(msg, coordinator.keypair.private_key)

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

    _orig = builtins.print
    builtins.print = lambda *a, **k: (_orig(*a, **k) if "Incoming" not in " ".join(str(x) for x in a) else None)

    divider(f"PHASE 1: DATA + SENTIMENT (parallel)")

    log("COORDINATOR", f"Analyzing {BOLD}{TICKER}{RESET} — dispatching 2 agents in parallel...")

    p1_start = time.time()
    data_result, sentiment_result = await asyncio.gather(
        call_worker(coordinator, session, tracker, "data-collector", "DATA",
                    worker_urls["data-collector"],
                    f"Fetch live financial data and key metrics for {TICKER}"),
        call_worker(coordinator, session, tracker, "sentiment-analyst", "SENTIMENT",
                    worker_urls["sentiment-analyst"],
                    f"Analyze current market sentiment, news, and analyst ratings for {TICKER}"),
    )
    p1_ms = (time.time() - p1_start) * 1000
    log("COORDINATOR", f"Phase 1 complete in {p1_ms:.0f}ms (parallel)")

    # Display real data
    if data_result["status"] == "success":
        d = data_result["result"]
        log("COORDINATOR", f"  {BOLD}Price:{RESET} ${d.get('current_price')} ({d.get('change_pct', 0):+.2f}%)")
        log("COORDINATOR", f"  {BOLD}PE:{RESET} {d.get('pe_ratio')}  |  {BOLD}Fwd PE:{RESET} {d.get('forward_pe')}  |  {BOLD}MCap:{RESET} ${d.get('market_cap_billions')}B")
        log("COORDINATOR", f"  {BOLD}52W:{RESET} ${d.get('price_52w_low')} — ${d.get('price_52w_high')}  |  {BOLD}Rev Growth:{RESET} {d.get('revenue_growth_yoy')}")
        log("COORDINATOR", f"  {BOLD}Margins:{RESET} Gross {d.get('gross_margin')} | Op {d.get('operating_margin')} | Net {d.get('profit_margin')}")
        log("COORDINATOR", f"  {BOLD}FCF:{RESET} ${d.get('free_cash_flow_billions')}B  |  {BOLD}ROE:{RESET} {d.get('return_on_equity')}  |  {BOLD}Beta:{RESET} {d.get('beta')}")

    if sentiment_result["status"] == "success":
        s = sentiment_result["result"]
        sent = s.get("overall_sentiment", "?").upper()
        sent_color = GREEN if "bull" in sent.lower() else (RED if "bear" in sent.lower() else YELLOW)
        log("COORDINATOR", f"  {BOLD}Sentiment:{RESET} {sent_color}{sent}{RESET} (confidence: {s.get('confidence')})")
        log("COORDINATOR", f"  {BOLD}Analysts:{RESET} {s.get('analyst_consensus')} ({s.get('analyst_count')} analysts)")
        log("COORDINATOR", f"  {BOLD}Targets:{RESET} ${s.get('price_target_low')} — ${s.get('price_target_mean')} — ${s.get('price_target_high')}")
        bd = s.get("recommendation_breakdown", {})
        if bd:
            log("COORDINATOR", f"  {BOLD}Breakdown:{RESET} {GREEN}Strong Buy:{bd.get('strong_buy',0)} Buy:{bd.get('buy',0)}{RESET} "
                f"Hold:{bd.get('hold',0)} {RED}Sell:{bd.get('sell',0)} Strong Sell:{bd.get('strong_sell',0)}{RESET}")
        for h in s.get("recent_headlines", [])[:3]:
            log("COORDINATOR", f"  {DIM}📰 {h[:80]}{RESET}")

    divider(f"PHASE 2: STRATEGY (sequential, uses real data)")

    log("COORDINATOR", "Synthesizing real data into briefing for strategist...")

    data_d = data_result.get("result", {}) if data_result["status"] == "success" else {}
    sent_d = sentiment_result.get("result", {}) if sentiment_result["status"] == "success" else {}

    parts = []
    if data_d:
        parts.append(f"[Financial Data — Live from Yahoo Finance]\n{_format_result_for_briefing(data_d)}")
    if sent_d:
        parts.append(f"[Sentiment & Analyst Data — Live]\n{_format_result_for_briefing(sent_d)}")
    briefing = "\n\n".join(parts)

    log("COORDINATOR", f"Briefing ready ({len(briefing)} chars) — sending to STRATEGY ADVISOR")

    p2_start = time.time()
    strategy_result = await call_worker(
        coordinator, session, tracker, "strategy-advisor", "STRATEGY",
        worker_urls["strategy-advisor"],
        f"Provide an investment recommendation for {TICKER} based on this REAL data:\n\n{briefing}",
        cost=0.002,
    )
    p2_ms = (time.time() - p2_start) * 1000

    builtins.print = _orig

    return {
        "data": data_result,
        "sentiment": sentiment_result,
        "strategy": strategy_result,
        "p1_ms": p1_ms, "p2_ms": p2_ms, "total_ms": p1_ms + p2_ms,
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
    _real_print(f"  {BOLD}  {TICKER} Market Research — ZyndAI Multi-Agent Pipeline{RESET}")
    _real_print(f"  {BOLD}{'═' * 64}{RESET}")
    _real_print()

    log("SYSTEM", f"Target: {BOLD}{TICKER}{RESET}")
    log("SYSTEM", f"AI Backend: {AI_MODE}")
    log("SYSTEM", f"Data Source: Yahoo Finance (live)")
    log("SYSTEM", f"Registry: {REGISTRY_URL}")

    tmpdir = tempfile.mkdtemp(prefix="zyndai_stock_")

    divider("BOOTING AGENTS")

    agents = {}
    agents["stock-coordinator"] = boot_agent(tmpdir, "stock-coordinator",
        "Orchestrates stock market research pipeline",
        ["orchestration", "market-research"], "orchestration", 7300)
    agents["data-collector"] = boot_agent(tmpdir, "data-collector",
        "Fetches live financial data from Yahoo Finance",
        ["financial-data", "stock-metrics", "yahoo-finance"], "finance", 7301)
    agents["sentiment-analyst"] = boot_agent(tmpdir, "sentiment-analyst",
        "Analyzes market sentiment from real news and analyst ratings",
        ["sentiment-analysis", "news-analysis", "analyst-ratings"], "analysis", 7302)
    agents["strategy-advisor"] = boot_agent(tmpdir, "strategy-advisor",
        "Produces investment recommendations from real market data",
        ["investment-strategy", "stock-recommendation"], "advisory", 7303)

    agents["data-collector"].register_handler(data_handler(agents["data-collector"]))
    agents["sentiment-analyst"].register_handler(sentiment_handler(agents["sentiment-analyst"]))
    agents["strategy-advisor"].register_handler(strategy_handler(agents["strategy-advisor"]))

    worker_urls = {n: base_url(a) for n, a in agents.items() if n != "stock-coordinator"}

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

    log("COORDINATOR", f"Searching {CYAN}{REGISTRY_URL}{RESET}...")
    coordinator = agents["stock-coordinator"]
    for wname, keyword in [("data-collector", "yahoo-finance"), ("sentiment-analyst", "sentiment-analysis"), ("strategy-advisor", "investment-strategy")]:
        try:
            found = coordinator.search_agents(keyword=keyword, limit=5)
            match = next((a for a in found if a.get("name") == wname), None)
            if match:
                score = match.get("score", 0)
                log("COORDINATOR", f'{GREEN}✓{RESET} search("{keyword}") → {BOLD}{match["name"]}{RESET}  score={score:.2f}')
        except Exception:
            pass

    results = asyncio.run(run_pipeline(coordinator, worker_urls))

    # ─── Final Output ─────────────────────────────────────────────────────

    divider("INVESTMENT RECOMMENDATION")

    if results["strategy"]["status"] == "success":
        s = results["strategy"]["result"]
        rec = s.get("recommendation", "?")
        rec_color = GREEN if rec == "BUY" else (YELLOW if rec == "HOLD" else RED)

        _real_print(f"    {BOLD}Ticker:{RESET}          {TICKER}")
        price = results["data"]["result"].get("current_price", "?") if results["data"]["status"] == "success" else "?"
        _real_print(f"    {BOLD}Current Price:{RESET}   ${price}")
        _real_print(f"    {BOLD}Recommendation:{RESET}  {rec_color}{BOLD}{rec}{RESET}")
        _real_print(f"    {BOLD}Confidence:{RESET}      {s.get('confidence', '?')}")
        _real_print(f"    {BOLD}Target Price:{RESET}    ${s.get('target_price', '?')}")
        _real_print(f"    {BOLD}Time Horizon:{RESET}    {s.get('time_horizon', '?')}")
        _real_print()
        _real_print(f"    {BOLD}Rationale:{RESET}")
        for line in textwrap.wrap(s.get("rationale", ""), 60):
            _real_print(f"      {line}")
        _real_print()
        if s.get("catalysts"):
            _real_print(f"    {BOLD}Catalysts:{RESET}")
            for c in s["catalysts"]:
                _real_print(f"      {GREEN}▲{RESET} {c}")
            _real_print()
        if s.get("risks"):
            _real_print(f"    {BOLD}Risks:{RESET}")
            for r in s["risks"]:
                _real_print(f"      {RED}▼{RESET} {r}")
    else:
        _real_print(f"    {RED}Strategy failed: {results['strategy'].get('error')}{RESET}")

    divider("PIPELINE METRICS")

    ts = results["task_summary"]
    log("SYSTEM", f"Tasks: {ts['total']} completed, {ts['by_status'].get('failed', 0)} failed")
    log("SYSTEM", f"Cost: ${ts['total_cost_usd']:.4f} USDC")
    log("SYSTEM", f"Phase 1 (parallel): {results['p1_ms']:.0f}ms")
    log("SYSTEM", f"Phase 2 (sequential): {results['p2_ms']:.0f}ms")
    log("SYSTEM", f"Total: {results['total_ms']:.0f}ms")

    _real_print()
    for a in agents.values():
        a.stop_heartbeat()

    _real_print(f"  {BOLD}{'═' * 64}{RESET}")
    _real_print(f"  {BOLD}  Analysis Complete{RESET}")
    _real_print(f"  {BOLD}{'═' * 64}{RESET}")
    _real_print()
    _real_print(f"  {DIM}Try another ticker: TICKER=TSLA uv run python examples/apple_stock_research.py{RESET}")
    _real_print()


if __name__ == "__main__":
    main()
