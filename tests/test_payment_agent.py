"""G19 wiring: ZyndAIAgent config -> payment gate. Opt-in, needs a price, honours env."""

from __future__ import annotations

import socket
import threading

import pytest
import requests
from flask import Flask, jsonify
from werkzeug.serving import make_server

from zyndai_agent.a2a.payment_gate import X402PaymentGate
from zyndai_agent.a2a.types import ZYND_PAYMENT_REQUIRED
from zyndai_agent.agent import AgentConfig, ZyndAIAgent
from zyndai_agent.ed25519_identity import generate_keypair, save_keypair

import base64
import json

NETWORK = "eip155:84532"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def facilitator_url():
    """A local facilitator that only answers /supported, so these tests never touch the network."""
    app = Flask("supported-only")

    @app.get("/supported")
    def supported():
        return jsonify(
            {"kinds": [{"x402Version": 2, "scheme": "exact", "network": NETWORK}], "extensions": [], "signers": {}}
        )

    srv = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture()
def keypair_path(tmp_path):
    p = tmp_path / "kp.json"
    save_keypair(generate_keypair(), str(p))
    return str(p)


@pytest.fixture()
def started(monkeypatch):
    agents: list[ZyndAIAgent] = []
    # A stray developer environment must not leak into these tests.
    monkeypatch.delenv("ZYND_ENFORCE_PAYMENT", raising=False)
    monkeypatch.delenv("X402_FACILITATOR_URL", raising=False)
    monkeypatch.delenv("X402_NETWORK", raising=False)
    monkeypatch.delenv("ZYND_AGENT_KEYPAIR_PATH", raising=False)
    monkeypatch.delenv("ZYND_SERVICE_KEYPAIR_PATH", raising=False)
    yield agents
    for a in agents:
        a.server.stop()


def run(started, keypair_path, facilitator_url, **cfg) -> ZyndAIAgent:
    config = AgentConfig(
        name="pay-test",
        keypair_path=keypair_path,
        server_host="127.0.0.1",
        server_port=_free_port(),
        payment_facilitator_url=facilitator_url,
        **cfg,
    )
    agent = ZyndAIAgent(config)
    agent.on_message(lambda _i, _t: "pong")
    agent.server.start()
    started.append(agent)
    return agent


def post(agent: ZyndAIAgent) -> requests.Response:
    return requests.post(
        agent.server.a2a_url,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "message/send",
            "params": {"message": {"kind": "message", "role": "user", "messageId": "m", "parts": [{"kind": "text", "text": "hi"}]}},
        },
    )


def challenge(resp: requests.Response) -> dict:
    return json.loads(base64.b64decode(resp.headers["PAYMENT-REQUIRED"]))


def test_a_price_alone_only_advertises_calls_stay_free(started, keypair_path, facilitator_url):
    a = run(started, keypair_path, facilitator_url, price="$0.01")
    assert post(a).status_code == 200


def test_enforce_payment_with_price_402s_to_the_agents_own_wallet(started, keypair_path, facilitator_url):
    a = run(started, keypair_path, facilitator_url, price="$0.01", enforce_payment=True)
    r = post(a)
    assert r.status_code == 402
    assert r.json()["error"]["code"] == ZYND_PAYMENT_REQUIRED
    req = challenge(r)["accepts"][0]
    assert req["payTo"] == a.pay_to_address
    assert req["amount"] == "10000"


def test_entity_pricing_works_as_the_price_source(started, keypair_path, facilitator_url):
    a = run(
        started, keypair_path, facilitator_url,
        entity_pricing={"base_price_usd": 0.25, "currency": "USDC"}, enforce_payment=True,
    )
    assert challenge(post(a))["accepts"][0]["amount"] == "250000"


def test_env_var_switches_it_on_without_code_changes(started, keypair_path, facilitator_url, monkeypatch):
    monkeypatch.setenv("ZYND_ENFORCE_PAYMENT", "1")
    a = run(started, keypair_path, facilitator_url, price="$0.01")
    assert post(a).status_code == 402


def test_explicit_false_beats_the_env_var(started, keypair_path, facilitator_url, monkeypatch):
    monkeypatch.setenv("ZYND_ENFORCE_PAYMENT", "1")
    a = run(started, keypair_path, facilitator_url, price="$0.01", enforce_payment=False)
    assert post(a).status_code == 200


def test_enforce_payment_without_a_price_fails_at_construction(keypair_path):
    with pytest.raises(ValueError, match="no price"):
        ZyndAIAgent(
            AgentConfig(name="x", keypair_path=keypair_path, server_host="127.0.0.1", server_port=_free_port(), enforce_payment=True)
        )


def test_the_gate_is_a_real_x402_payment_gate(started, keypair_path, facilitator_url):
    a = run(started, keypair_path, facilitator_url, price="$0.05", enforce_payment=True)
    assert isinstance(a.server.gate, X402PaymentGate)
    assert a.server.gate.price_usd == 0.05
