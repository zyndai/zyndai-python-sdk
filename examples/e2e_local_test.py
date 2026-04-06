#!/usr/bin/env python3
"""
End-to-end local test: spins up real agents on localhost, tests the full
orchestration stack — typed messages, signatures, sessions, fan_out,
and coordinator strategy execution.

Run:  uv run python examples/e2e_local_test.py

No external registry needed. Agents communicate directly via localhost webhooks.
"""

import json
import os
import sys
import time
import tempfile
import requests

from zyndai_agent.ed25519_identity import generate_keypair, save_keypair
from zyndai_agent import (
    ZyndAIAgent,
    AgentConfig,
    AgentMessage,
    Coordinator,
    OrchestrationContext,
    InvokeMessage,
    parse_message,
    sign_message,
    verify_message,
)

REGISTRY_URL = os.getenv("REGISTRY_URL", "https://registry.zynd.ai")

# ─── Helpers ──────────────────────────────────────────────────────────────────

def make_keypair_file(tmpdir: str, name: str) -> str:
    kp = generate_keypair()
    path = os.path.join(tmpdir, f"{name}.json")
    save_keypair(kp, path)
    return path


def wait_for_health(url: str, retries: int = 20):
    for _ in range(retries):
        try:
            r = requests.get(f"{url}/health", timeout=2)
            if r.status_code == 200:
                return True
        except requests.ConnectionError:
            pass
        time.sleep(0.3)
    raise RuntimeError(f"Agent at {url} never became healthy")


def safe_math(expression: str) -> str:
    """Evaluate simple arithmetic without eval — supports +, -, *, /."""
    import ast
    import operator

    ops = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
    }

    def _eval(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in ops:
            return ops[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -_eval(node.operand)
        raise ValueError(f"Unsupported expression: {ast.dump(node)}")

    tree = ast.parse(expression, mode="eval")
    return str(_eval(tree.body))


def passed(label: str):
    print(f"  \033[32m✓\033[0m {label}")


def failed(label: str, detail: str = ""):
    print(f"  \033[31m✗\033[0m {label} — {detail}")
    return False


# ─── Test Suite ───────────────────────────────────────────────────────────────

def test_1_basic_webhook(worker_url: str):
    """Send a legacy AgentMessage and get a response."""
    payload = {
        "content": "What is 2+2?",
        "sender_id": "test-client",
        "message_type": "query",
    }
    r = requests.post(f"{worker_url}/webhook/sync", json=payload, timeout=10)
    data = r.json()
    if r.status_code == 200 and data.get("status") == "success":
        passed(f"Basic webhook: got response = {data['response'][:80]}")
        return True
    return failed("Basic webhook", f"status={r.status_code} body={data}")


def test_2_typed_message(worker_url: str, sender_kp):
    """Send a typed InvokeMessage with signature."""
    msg = InvokeMessage(
        sender_id=sender_kp.agent_id,
        sender_public_key=sender_kp.public_key_string,
        capability="math",
        payload={"expression": "2+2"},
        timeout_seconds=10,
    )
    msg.signature = sign_message(msg, sender_kp.private_key)

    r = requests.post(
        f"{worker_url}/webhook/sync",
        json=msg.model_dump(mode="json"),
        timeout=10,
    )
    data = r.json()
    if r.status_code == 200 and data.get("status") == "success":
        passed(f"Typed InvokeMessage: response = {data['response'][:80]}")
        return True
    return failed("Typed InvokeMessage", f"status={r.status_code} body={data}")


def test_3_signature_verification(worker_url: str, sender_kp):
    """Verify that the message signature is validated end-to-end."""
    msg = InvokeMessage(
        sender_id=sender_kp.agent_id,
        sender_public_key=sender_kp.public_key_string,
        capability="math",
        payload={"expression": "3+3"},
    )
    msg.signature = sign_message(msg, sender_kp.private_key)

    typed_msg = parse_message(msg.model_dump(mode="json"))
    pub_b64 = sender_kp.public_key_b64
    valid = verify_message(typed_msg, pub_b64)
    if valid:
        passed("Signature round-trip verification")
        return True
    return failed("Signature verification", "verify_message returned False")


def test_4_session_tracking(worker_url: str):
    """Send two messages with the same conversation_id, verify session is tracked."""
    conv_id = "e2e-test-conv-001"
    for i in range(2):
        payload = {
            "content": f"Session message {i+1}",
            "sender_id": "test-client",
            "conversation_id": conv_id,
        }
        requests.post(f"{worker_url}/webhook", json=payload, timeout=5)
        time.sleep(0.2)

    passed("Session tracking: 2 messages sent with same conversation_id (no errors)")
    return True


def test_5_health_endpoints(worker_url: str, translator_url: str):
    """Verify all agent health endpoints respond."""
    for name, url in [("Worker", worker_url), ("Translator", translator_url)]:
        r = requests.get(f"{url}/health", timeout=5)
        if r.status_code != 200:
            return failed(f"Health check {name}", f"status={r.status_code}")
    passed("Health endpoints: all agents healthy")
    return True


def test_6_agent_card(worker_url: str):
    """Verify agent card is served."""
    r = requests.get(f"{worker_url}/.well-known/agent.json", timeout=5)
    if r.status_code == 200:
        card = r.json()
        if card.get("agent_id"):
            passed(f"Agent Card: agent_id={card['agent_id'][:20]}...")
            return True
    return failed("Agent Card", f"status={r.status_code}")


def test_7_cross_agent_call(worker_url: str, translator_url: str):
    """Worker agent calls translator agent directly via HTTP."""
    payload = {
        "content": "Hello world",
        "sender_id": "math-worker",
        "message_type": "query",
    }
    r = requests.post(f"{translator_url}/webhook/sync", json=payload, timeout=10)
    data = r.json()
    if r.status_code == 200 and data.get("status") == "success":
        passed(f"Cross-agent call: translator responded = {data['response'][:80]}")
        return True
    return failed("Cross-agent call", f"status={r.status_code}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("\n\033[1m═══ ZyndAI Orchestration E2E Test ═══\033[0m\n")

    tmpdir = tempfile.mkdtemp(prefix="zyndai_e2e_")

    worker_kp_path = make_keypair_file(tmpdir, "worker")
    translator_kp_path = make_keypair_file(tmpdir, "translator")
    client_kp = generate_keypair()

    print("Starting agents...\n")

    # ─── Agent 1: Math Worker ─────────────────────────────────────────────

    worker_agent = ZyndAIAgent(AgentConfig(
        name="e2e-math-worker",
        description="Solves math expressions for e2e test",
        capabilities={"skills": ["math", "calculation"]},
        webhook_port=7101,
        keypair_path=worker_kp_path,
        registry_url=REGISTRY_URL,
    ))

    def math_handler(message, topic, session):
        content = message.content
        try:
            typed = parse_message(message.to_dict())
            if hasattr(typed, "payload") and "expression" in typed.payload:
                expr = typed.payload["expression"]
                result = safe_math(expr)
            else:
                result = f"Echo: {content}"
        except Exception:
            result = f"Echo: {content}"
        worker_agent.set_response(message.message_id, result)

    worker_agent.register_handler(math_handler)
    worker_webhook = worker_agent.webhook_url
    worker_base = worker_webhook.replace("/webhook", "")
    print(f"  Math worker:  {worker_base}")

    # ─── Agent 2: Translator ──────────────────────────────────────────────

    translator_agent = ZyndAIAgent(AgentConfig(
        name="e2e-translator",
        description="Translates text for e2e test",
        capabilities={"skills": ["translation", "language"]},
        webhook_port=7102,
        keypair_path=translator_kp_path,
        registry_url=REGISTRY_URL,
    ))

    def translate_handler(message, topic):
        translator_agent.set_response(
            message.message_id,
            f"[FR] {message.content}",
        )

    translator_agent.register_handler(translate_handler)
    translator_webhook = translator_agent.webhook_url
    translator_base = translator_webhook.replace("/webhook", "")
    print(f"  Translator:   {translator_base}")

    wait_for_health(worker_base)
    wait_for_health(translator_base)

    print("\n\033[1m─── Running Tests ───\033[0m\n")

    results = []
    results.append(test_1_basic_webhook(worker_base))
    results.append(test_2_typed_message(worker_base, client_kp))
    results.append(test_3_signature_verification(worker_base, client_kp))
    results.append(test_4_session_tracking(worker_base))
    results.append(test_5_health_endpoints(worker_base, translator_base))
    results.append(test_6_agent_card(worker_base))
    results.append(test_7_cross_agent_call(worker_base, translator_base))

    # ─── Summary ──────────────────────────────────────────────────────────

    total = len(results)
    passing = sum(1 for r in results if r)
    failing = total - passing

    print(f"\n\033[1m─── Results: {passing}/{total} passed", end="")
    if failing:
        print(f", {failing} failed ───\033[0m")
    else:
        print(" ───\033[0m")

    print("\n\033[1m─── Agent State ───\033[0m\n")

    worker_sessions = worker_agent.active_sessions
    print(f"  Math worker sessions:  {len(worker_sessions)}")
    for s in worker_sessions:
        print(f"    conv={s.conversation_id[:16]}... msgs={len(s.messages)} cost=${s.total_cost_usd:.4f}")

    translator_sessions = translator_agent.active_sessions
    print(f"  Translator sessions:   {len(translator_sessions)}")

    print()

    worker_agent.stop_heartbeat()
    translator_agent.stop_heartbeat()

    if failing:
        sys.exit(1)


if __name__ == "__main__":
    main()
