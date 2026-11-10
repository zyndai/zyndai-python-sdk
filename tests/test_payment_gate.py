"""G19: an SDK-hosted agent asks for x402 payment, and the SDK client pays it.

Runs a real A2AServer, a real A2AClient and an in-process facilitator that checks the
EIP-712 signature of every payment, so the signing on one side and the verification on the
other are tested against each other (not against fixtures).
"""

from __future__ import annotations

import base64
import json
import socket
import threading
import time
from typing import Any, Callable, Optional

import pytest
import requests
from eth_account import Account
from eth_account.messages import encode_typed_data
from flask import Flask, jsonify, request
from werkzeug.serving import make_server

from zyndai_agent.a2a.client import A2AClient
from zyndai_agent.a2a.payment_client import ClientPaymentOptions, PaymentRefusedError
from zyndai_agent.a2a.payment_gate import X402PaymentGate
from zyndai_agent.a2a.server import A2AServer
from zyndai_agent.ed25519_identity import generate_keypair
from zyndai_agent.payment import X402PaymentProcessor

NETWORK = "eip155:84532"
USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"


# --- fake facilitator -----------------------------------------------------------------------


class Facilitator:
    """Verifies the EIP-712 signature of every payment, counts calls, and can misbehave."""

    def __init__(self) -> None:
        self.verifies = 0
        self.settles = 0
        self.reject_verify = False
        self.fail_settle = False
        self.down = False
        app = Flask("fake-facilitator")

        @app.get("/supported")
        def supported() -> Any:
            return jsonify(
                {
                    "kinds": [{"x402Version": 2, "scheme": "exact", "network": NETWORK}],
                    "extensions": [],
                    "signers": {},
                }
            )

        @app.post("/verify")
        def verify() -> Any:
            self.verifies += 1
            if self.down:
                return jsonify({"error": "boom"}), 500
            body = request.get_json()
            p, r = body["paymentPayload"], body["paymentRequirements"]
            a = p["payload"]["authorization"]
            signable = encode_typed_data(
                full_message={
                    "types": {
                        "EIP712Domain": [
                            {"name": "name", "type": "string"},
                            {"name": "version", "type": "string"},
                            {"name": "chainId", "type": "uint256"},
                            {"name": "verifyingContract", "type": "address"},
                        ],
                        "TransferWithAuthorization": [
                            {"name": "from", "type": "address"},
                            {"name": "to", "type": "address"},
                            {"name": "value", "type": "uint256"},
                            {"name": "validAfter", "type": "uint256"},
                            {"name": "validBefore", "type": "uint256"},
                            {"name": "nonce", "type": "bytes32"},
                        ],
                    },
                    "primaryType": "TransferWithAuthorization",
                    "domain": {
                        "name": r["extra"]["name"],
                        "version": r["extra"]["version"],
                        "chainId": 84532,
                        "verifyingContract": r["asset"],
                    },
                    "message": {
                        "from": a["from"],
                        "to": a["to"],
                        "value": int(a["value"]),
                        "validAfter": int(a["validAfter"]),
                        "validBefore": int(a["validBefore"]),
                        "nonce": a["nonce"],
                    },
                }
            )
            signer = Account.recover_message(signable, signature=p["payload"]["signature"])
            ok = (
                signer.lower() == a["from"].lower()
                and a["value"] == r["amount"]
                and a["to"].lower() == r["payTo"].lower()
            )
            if self.reject_verify or not ok:
                return jsonify(
                    {
                        "isValid": False,
                        "invalidReason": "insufficient_funds" if self.reject_verify else "invalid_signature",
                    }
                )
            return jsonify({"isValid": True, "payer": a["from"]})

        @app.post("/settle")
        def settle() -> Any:
            self.settles += 1
            if self.fail_settle:
                return jsonify(
                    {"success": False, "errorReason": "insufficient_funds", "transaction": "", "network": NETWORK}
                )
            payer = request.get_json()["paymentPayload"]["payload"]["authorization"]["from"]
            return jsonify(
                {"success": True, "transaction": f"0xtx{self.settles}", "network": NETWORK, "payer": payer}
            )

        self._srv = make_server("127.0.0.1", 0, app, threaded=True)
        self.url = f"http://127.0.0.1:{self._srv.server_port}"
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._srv.shutdown()


# --- helpers --------------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Agent:
    def __init__(self, server: A2AServer, pay_to: str) -> None:
        self.server = server
        self.url = server.a2a_url
        self.pay_to = pay_to


def start_agent(
    fac: Optional[Facilitator],
    handler: Optional[Callable[..., Any]] = None,
    price: str = "$0.01",
) -> Agent:
    kp = generate_keypair()
    pay_to = X402PaymentProcessor(ed25519_private_key_bytes=kp.private_key_bytes).account.address
    payment = (
        {"price": price, "pay_to": pay_to, "network": NETWORK, "facilitator_url": fac.url}
        if fac
        else None
    )
    server = A2AServer(
        entity_id=kp.entity_id,
        keypair=kp,
        agent_card_builder=lambda: {"name": "paid", "url": "http://example.test/a2a/v1"},
        host="127.0.0.1",
        port=_free_port(),
        payment=payment,
    )
    server.set_handler(handler or (lambda _inp, _task: "pong"))
    server.start()
    return Agent(server, pay_to)


def new_client(payment: Any = None) -> A2AClient:
    kp = generate_keypair()
    return A2AClient(kp, kp.entity_id, payment=payment)


def rpc_send(text: str = "hi", method: str = "message/send", message_id: str = "m1") -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": {
            "message": {
                "kind": "message",
                "role": "user",
                "messageId": message_id,
                "parts": [{"kind": "text", "text": text}],
            }
        },
    }


def decode_b64_json(value: str) -> dict:
    return json.loads(base64.b64decode(value).decode())


@pytest.fixture()
def fac():
    f = Facilitator()
    yield f
    f.close()


@pytest.fixture()
def agents():
    started: list[Agent] = []
    yield started
    for a in started:
        a.server.stop()


@pytest.fixture()
def start(fac, agents):
    def _start(**kw: Any) -> Agent:
        a = start_agent(fac, **kw)
        agents.append(a)
        return a

    return _start


# --- tests ----------------------------------------------------------------------------------


class TestServerAsksForPayment:
    def test_unpaid_call_gets_402_and_a_payment_required_challenge(self, fac, start):
        agent = start()
        resp = requests.post(agent.url, json=rpc_send())
        assert resp.status_code == 402
        challenge = decode_b64_json(resp.headers["PAYMENT-REQUIRED"])
        assert challenge["x402Version"] == 2
        assert len(challenge["accepts"]) == 1
        req = challenge["accepts"][0]
        assert req["scheme"] == "exact"
        assert req["network"] == NETWORK
        assert req["amount"] == "10000"  # $0.01 of a 6-decimals token
        assert req["payTo"] == agent.pay_to
        assert req["asset"].lower() == USDC.lower()
        assert resp.json()["error"]["code"] == -32103
        assert fac.settles == 0

    def test_without_payment_enabled_the_client_surfaces_the_402_as_an_error(self, start):
        agent = start()
        with pytest.raises(RuntimeError, match="402"):
            new_client().sync(agent.url, text="hi")

    def test_paying_client_gets_the_answer_and_payment_settles_once_after_the_job(self, fac, start):
        ran: list[int] = []

        def handler(_inp, _task):
            ran.append(1)
            assert fac.settles == 0  # not charged before the work is done
            return "pong"

        agent = start(handler=handler)
        task = new_client(True).sync(agent.url, text="ping")
        assert task["status"]["state"] == "completed"
        assert len(ran) == 1
        assert fac.verifies == 1
        assert fac.settles == 1
        x402 = task["metadata"]["x402"]
        assert x402["settled"] is True
        assert x402["transaction"] == "0xtx1"
        assert x402["network"] == NETWORK

    def test_works_through_ask(self, fac, start):
        agent = start()
        assert "pong" in new_client(True).ask(agent.url, "ping")
        assert fac.settles == 1

    def test_reply_carries_payment_response_header(self, fac, start):
        agent = start()
        seen: dict[str, str] = {}
        real = requests.post

        def spy(*a: Any, **kw: Any) -> requests.Response:
            r = real(*a, **kw)
            if "PAYMENT-RESPONSE" in r.headers:
                seen.update(r.headers)
            return r

        import zyndai_agent.a2a.client as client_mod

        orig, client_mod.requests.post = client_mod.requests.post, spy
        try:
            new_client(True).sync(agent.url, text="ping")
        finally:
            client_mod.requests.post = orig
        done = decode_b64_json(seen["PAYMENT-RESPONSE"])
        assert done["success"] is True and done["transaction"] == "0xtx1"

    def test_does_not_charge_for_a_job_that_fails(self, fac, start):
        def boom(_inp, _task):
            raise RuntimeError("nope")

        agent = start(handler=boom)
        task = new_client(True).sync(agent.url, text="ping")
        assert task["status"]["state"] == "failed"
        assert fac.verifies == 1
        assert fac.settles == 0

    def test_client_refuses_to_pay_more_than_its_cap_and_the_job_never_runs(self, fac, start):
        ran: list[int] = []
        agent = start(price="$0.50", handler=lambda _i, _t: ran.append(1))
        with pytest.raises(PaymentRefusedError, match="Refusing to pay"):
            new_client(ClientPaymentOptions(max_payment_usd=0.1)).sync(agent.url, text="x")
        assert ran == []
        assert fac.verifies == 0
        # Raising the cap lets the same call through.
        task = new_client(ClientPaymentOptions(max_payment_usd=1.0)).sync(agent.url, text="x")
        assert task["status"]["state"] == "completed"

    def test_rejects_a_payment_the_facilitator_refuses_with_a_402_and_no_job(self, fac, start):
        fac.reject_verify = True
        ran: list[int] = []
        agent = start(handler=lambda _i, _t: ran.append(1))
        with pytest.raises(RuntimeError, match="402"):
            new_client(True).sync(agent.url, text="x")
        assert ran == []
        assert fac.settles == 0

    def test_answers_503_when_the_facilitator_is_down(self, fac, start):
        fac.down = True
        agent = start()
        with pytest.raises(RuntimeError, match="503"):
            new_client(True).sync(agent.url, text="x")

    def test_withholds_the_result_when_settlement_fails_after_the_job(self, fac, start):
        fac.fail_settle = True
        agent = start(handler=lambda _i, _t: "secret")
        with pytest.raises(RuntimeError, match="402"):
            new_client(True).sync(agent.url, text="x")
        assert fac.settles == 1

    def test_one_payment_proof_cannot_pay_for_two_jobs(self, fac, start, monkeypatch):
        gate = threading.Event()

        def slow(_inp, _task):
            gate.wait(10)
            return "pong"

        agent = start(handler=slow)
        captured: list[str] = []
        real = requests.post

        def tap(*a: Any, **kw: Any) -> requests.Response:
            sig = (kw.get("headers") or {}).get("PAYMENT-SIGNATURE")
            if sig:
                captured.append(sig)
            return real(*a, **kw)

        import zyndai_agent.a2a.client as client_mod

        monkeypatch.setattr(client_mod.requests, "post", tap)
        result: dict[str, Any] = {}
        t = threading.Thread(
            target=lambda: result.setdefault("task", new_client(True).sync(agent.url, text="a")),
            daemon=True,
        )
        t.start()
        for _ in range(100):
            if captured:
                break
            time.sleep(0.05)
        time.sleep(0.3)  # first call is now mid-job, holding its proof
        assert len(captured) == 1

        replay = real(
            agent.url,
            json=rpc_send("b", message_id="m2"),
            headers={"PAYMENT-SIGNATURE": captured[0]},
        )
        assert replay.status_code == 402
        gate.set()
        t.join(10)
        assert result["task"]["status"]["state"] == "completed"
        assert fac.settles == 1

    def test_blocking_false_returns_at_once_and_settlement_lands_in_task_metadata(self, fac, start):
        agent = start()
        client = new_client(True)
        started = client.sync(agent.url, text="x", blocking=False)
        for _ in range(100):
            if fac.settles:
                break
            time.sleep(0.05)
        assert fac.settles == 1
        for _ in range(40):
            got = requests.post(
                agent.url,
                json={"jsonrpc": "2.0", "id": 3, "method": "tasks/get", "params": {"id": started["id"]}},
            ).json()["result"]
            if (got.get("metadata") or {}).get("x402"):
                break
            time.sleep(0.05)
        assert got["metadata"]["x402"]["settled"] is True

    def test_a_follow_up_on_a_parked_task_rides_on_the_first_payment(self, fac, start):
        def handler(_inp, task):
            more = task.ask("which one?")
            return f"got {more.message.content}"

        agent = start(handler=handler)
        parked = new_client(True).sync(agent.url, text="start")
        assert parked["status"]["state"] == "input-required"
        assert fac.settles == 0  # not done yet, not charged yet

        # The follow-up carries no payment at all (plain client, no payer).
        new_client().sync(
            agent.url, text="blue", task_id=parked["id"], context_id=parked["contextId"]
        )
        # (The Python server's resume reply can still show the pre-resume state, so read the
        # outcome from tasks/get rather than from that reply.)
        state = ""
        for _ in range(100):
            state = requests.post(
                agent.url,
                json={"jsonrpc": "2.0", "id": 9, "method": "tasks/get", "params": {"id": parked["id"]}},
            ).json()["result"]["status"]["state"]
            if state == "completed":
                break
            time.sleep(0.05)
        assert state == "completed"
        for _ in range(100):
            if fac.settles:
                break
            time.sleep(0.05)
        assert fac.verifies == 1  # the follow-up was not asked to pay again
        assert fac.settles == 1

    def test_free_agents_are_untouched(self, fac, agents):
        free = start_agent(None)
        agents.append(free)
        task = new_client().sync(free.url, text="x")
        assert task["status"]["state"] == "completed"
        assert fac.verifies + fac.settles == 0

    def test_message_stream_is_gated_too(self, fac, start):
        agent = start()
        resp = requests.post(
            agent.url, json=rpc_send(method="message/stream", message_id="m4"),
            headers={"Accept": "text/event-stream"},
        )
        assert resp.status_code == 402
        assert resp.headers.get("PAYMENT-REQUIRED")


class TestClientResignsOnRetry:
    def test_the_paid_retry_carries_a_freshly_signed_message(self, fac):
        """Stand-in for a server that checks x-zynd-auth *before* payment (the n8n Gateway does)."""
        gate = X402PaymentGate(
            price="$0.01", pay_to="0x" + "2" * 40, network=NETWORK, facilitator_url=fac.url
        )
        seen: list[dict] = []
        app = Flask("gateway-like")

        @app.post("/a2a/v1")
        def a2a() -> Any:
            body = request.get_json()
            meta = body["params"]["message"]["metadata"]["x-zynd-auth"]
            seen.append({"nonce": meta["nonce"], "paid": gate.has_payment(request.headers)})
            if sum(1 for s in seen if s["nonce"] == meta["nonce"]) > 1:
                return jsonify({"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32101, "message": "nonce already used"}}), 401
            if not gate.has_payment(request.headers):
                err = gate.challenge("http://x/a2a/v1")
                resp = jsonify({"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32103, "message": str(err)}})
                resp.status_code = 402
                resp.headers["PAYMENT-REQUIRED"] = err.header
                return resp
            return jsonify(
                {"jsonrpc": "2.0", "id": body["id"], "result": {"kind": "task", "id": "t1", "contextId": "c1", "status": {"state": "completed"}}}
            )

        srv = make_server("127.0.0.1", 0, app, threaded=True)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{srv.server_port}/a2a/v1"
            task = new_client(True).sync(url, text="hi")
            assert task["status"]["state"] == "completed"
            assert [s["paid"] for s in seen] == [False, True]
            assert seen[1]["nonce"] != seen[0]["nonce"]
        finally:
            srv.shutdown()


class TestGateConfig:
    def test_rejects_a_bad_gate_configuration_up_front(self):
        with pytest.raises(ValueError, match="price"):
            X402PaymentGate(price=0, pay_to="0x" + "1" * 40)
        with pytest.raises(ValueError, match="pay_to"):
            X402PaymentGate(price="$0.01", pay_to="nope")

    @pytest.mark.parametrize(
        "raw,expected",
        [("$0.01", 0.01), ("0.25 USDC", 0.25), (0.5, 0.5), ("free", None), (0, None), (None, None), ("-1", None), ("$-1", None), (-0.5, None)],
    )
    def test_price_parsing(self, raw, expected):
        from zyndai_agent.a2a.payment_gate import parse_price_usd

        assert parse_price_usd(raw) == expected
