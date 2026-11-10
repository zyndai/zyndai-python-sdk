"""Outbound A2A client.

Mirrors `zyndai-ts-sdk/src/a2a/client.ts`. Sync API since Python users
typically integrate via Flask handlers / sync code paths. Streaming is
implemented as a generator yielding StreamEvent dicts.
"""

import json
import uuid
from dataclasses import dataclass
from typing import Any, Generator, Literal, Optional

import requests

from zyndai_agent.a2a.adapter import (
    Attachment,
    task_reply_text,
    to_a2a_message,
)
from zyndai_agent.a2a.auth import sign_message
from zyndai_agent.a2a.payment_client import A2APayer, ClientPaymentOptions
from zyndai_agent.ed25519_identity import Ed25519Keypair


class A2AError(Exception):
    """Server returned a JSON-RPC error envelope. `code` and `data`
    are populated from the error block.
    """

    def __init__(self, rpc_error: dict[str, Any]) -> None:
        err = rpc_error.get("error", {})
        super().__init__(f"A2A error {err.get('code')}: {err.get('message')}")
        self.code = err.get("code")
        self.data = err.get("data")


class A2AClient:
    """Outbound A2A client."""

    def __init__(
        self,
        keypair: Ed25519Keypair,
        entity_id: str,
        *,
        fqan: Optional[str] = None,
        developer_proof: Optional[dict[str, Any]] = None,
        timeout: float = 5 * 60,
        payment: "ClientPaymentOptions | bool | None" = None,
    ) -> None:
        """
        `payment`: pay x402 challenges (HTTP 402) automatically, once per call, up to
        `max_payment_usd` (default $0.10). Off unless set: without it a paid agent's 402 is
        raised as an error and nothing is signed. Pass `True` for the defaults, which pay from
        the wallet derived from `keypair`, or a `ClientPaymentOptions`.
        """
        self.keypair = keypair
        self.entity_id = entity_id
        self.fqan = fqan
        self.developer_proof = developer_proof
        self.timeout = timeout
        self._payer: Optional[A2APayer] = (
            A2APayer(keypair, None if payment is True else payment) if payment else None
        )

    def _post(
        self,
        url: str,
        make_body: Any,
        *,
        headers: dict[str, str],
        timeout: float,
        stream: bool = False,
    ) -> requests.Response:
        """POST that survives an x402 challenge: on HTTP 402, if payment is enabled, sign the
        server's requirements and send the call once more with `PAYMENT-SIGNATURE`.

        `make_body` is called again for the retry so the A2A message is signed afresh. Some
        servers (the Zynd n8n Gateway among them) check `x-zynd-auth` before payment and burn
        its nonce on the unpaid attempt, so resending the same signed message would be
        refused as a replay.
        """
        first = requests.post(
            url, json=make_body(), headers=headers, timeout=timeout, stream=stream
        )
        if first.status_code != 402 or self._payer is None:
            return first
        try:
            challenge = first.json()
        except ValueError:
            challenge = None
        paid = self._payer.answer(first.headers, challenge)
        first.close()
        return requests.post(
            url,
            json=make_body(),
            headers={**headers, **paid},
            timeout=timeout,
            stream=stream,
        )

    # -------------------------------------------------------------------------
    # Sync — single request/response, returns final Task dict.
    # -------------------------------------------------------------------------

    def sync(
        self,
        url: str,
        *,
        transport: "A2ATransport" = "JSONRPC",
        text: Optional[str] = None,
        data: Optional[dict[str, Any]] = None,
        attachments: Optional[list[Attachment]] = None,
        task_id: Optional[str] = None,
        context_id: Optional[str] = None,
        blocking: bool = True,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """Send the message and return the final Task dict.

        `transport`:
          - "JSONRPC"   (default) — signed JSON-RPC `message/send` envelope
          - "HTTP+JSON"           — plain POST of MessageSendParams; response
                                    is a Task or Message body directly.
        """
        if transport == "HTTP+JSON":
            return self._sync_http_json(
                url,
                text=text,
                data=data,
                attachments=attachments,
                task_id=task_id,
                context_id=context_id,
                blocking=blocking,
                timeout=timeout,
            )

        def make_body() -> dict[str, Any]:
            return {
                "jsonrpc": "2.0",
                "id": str(uuid.uuid4()),
                "method": "message/send",
                "params": {
                    "message": self._build_message(
                        text=text,
                        data=data,
                        attachments=attachments,
                        task_id=task_id,
                        context_id=context_id,
                    ),
                    "configuration": {"blocking": blocking},
                },
            }

        resp = self._post(
            url,
            make_body,
            headers={"Content-Type": "application/json"},
            timeout=timeout or self.timeout,
        )
        if not resp.ok:
            raise RuntimeError(f"A2A sync HTTP {resp.status_code}: {resp.text}")

        body = resp.json()
        if "error" in body:
            raise A2AError(body)

        result = body.get("result")
        if isinstance(result, dict) and result.get("kind") == "task":
            return result
        return self._wrap_message_as_task(result, context_id)

    def _sync_http_json(
        self,
        url: str,
        *,
        text: Optional[str],
        data: Optional[dict[str, Any]],
        attachments: Optional[list[Attachment]],
        task_id: Optional[str],
        context_id: Optional[str],
        blocking: bool,
        timeout: Optional[float],
    ) -> dict[str, Any]:
        """HTTP+JSON transport — POSTs MessageSendParams directly (no JSON-RPC
        envelope) and expects a Task or Message body back."""
        def make_body() -> dict[str, Any]:
            return {
                "message": self._build_message(
                    text=text,
                    data=data,
                    attachments=attachments,
                    task_id=task_id,
                    context_id=context_id,
                ),
                "configuration": {"blocking": blocking},
            }

        resp = self._post(
            url,
            make_body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=timeout or self.timeout,
        )
        if not resp.ok:
            raise RuntimeError(
                f"A2A sync HTTP+JSON {resp.status_code}: {resp.text}"
            )
        result = resp.json()
        if isinstance(result, dict) and result.get("kind") == "task":
            return result
        return self._wrap_message_as_task(result, context_id)

    @staticmethod
    def _wrap_message_as_task(
        msg: Any, context_id: Optional[str]
    ) -> dict[str, Any]:
        """Wrap a bare Message response in a synthetic completed Task so
        callers always see a uniform shape."""
        return {
            "kind": "task",
            "id": str(uuid.uuid4()),
            "contextId": context_id or str(uuid.uuid4()),
            "status": {"state": "completed"},
            "history": [msg] if msg else [],
            "artifacts": [],
        }

    # -------------------------------------------------------------------------
    # Stream — yields StreamEvent dicts until terminal `final: True`.
    # -------------------------------------------------------------------------

    def stream(
        self,
        url: str,
        *,
        text: Optional[str] = None,
        data: Optional[dict[str, Any]] = None,
        attachments: Optional[list[Attachment]] = None,
        task_id: Optional[str] = None,
        context_id: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Generator[dict[str, Any], None, None]:
        """Send `message/stream` and yield events until the SSE stream
        closes (typically a status-update with final=True).
        """
        def make_body() -> dict[str, Any]:
            return {
                "jsonrpc": "2.0",
                "id": str(uuid.uuid4()),
                "method": "message/stream",
                "params": {
                    "message": self._build_message(
                        text=text,
                        data=data,
                        attachments=attachments,
                        task_id=task_id,
                        context_id=context_id,
                    )
                },
            }

        with self._post(
            url,
            make_body,
            stream=True,
            timeout=timeout or 30 * 60,
            headers={
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
        ) as resp:
            if not resp.ok:
                raise RuntimeError(
                    f"A2A stream HTTP {resp.status_code}: {resp.text}"
                )
            buffer = ""
            for chunk in resp.iter_content(chunk_size=4096, decode_unicode=True):
                if not chunk:
                    continue
                buffer += chunk
                while "\n\n" in buffer:
                    frame, buffer = buffer.split("\n\n", 1)
                    for line in frame.split("\n"):
                        if not line.startswith("data:"):
                            continue
                        data_str = line[len("data:") :].strip()
                        if not data_str:
                            continue
                        try:
                            parsed = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        if "error" in parsed:
                            raise A2AError(parsed)
                        ev = parsed.get("result")
                        if ev:
                            yield ev
                            if (
                                isinstance(ev, dict)
                                and ev.get("kind") == "status-update"
                                and ev.get("final")
                            ):
                                return

    # -------------------------------------------------------------------------
    # Card-based dispatch + ask convenience
    # -------------------------------------------------------------------------

    def call_via_card(
        self,
        card_or_base_url: str,
        *,
        text: Optional[str] = None,
        data: Optional[dict[str, Any]] = None,
        attachments: Optional[list[Attachment]] = None,
        task_id: Optional[str] = None,
        context_id: Optional[str] = None,
        timeout: Optional[float] = None,
        prefer_transport: str = "auto",
    ) -> dict[str, Any]:
        """Resolve an agent card URL → {transport, url}, then sync().

        Pass `prefer_transport` to force a specific transport; default
        ("auto") follows the card's `preferredTransport`.
        """
        resolved = resolve_transport(
            card_or_base_url, prefer=prefer_transport, timeout=timeout or 10
        )
        return self.sync(
            resolved.url,
            transport=resolved.transport,
            text=text,
            data=data,
            attachments=attachments,
            task_id=task_id,
            context_id=context_id,
            timeout=timeout,
        )

    def ask(
        self,
        target: str,
        text: str,
        *,
        task_id: Optional[str] = None,
        context_id: Optional[str] = None,
    ) -> str:
        """Convenience: call another agent and return its reply text.

        Encapsulates (sync → read artifacts → join text/data parts).
        Use this from inside an LLM tool — it deliberately does NOT
        expose `task.history` (which contains the caller's own outbound
        message echoed back, which would cause LLM tool loops).
        """
        if "/a2a/" in target:
            task = self.sync(
                target,
                text=text,
                task_id=task_id,
                context_id=context_id,
            )
        else:
            task = self.call_via_card(
                target,
                text=text,
                task_id=task_id,
                context_id=context_id,
            )
        return task_reply_text(task)

    # -------------------------------------------------------------------------
    # Internals
    # -------------------------------------------------------------------------

    def _build_message(
        self,
        *,
        text: Optional[str],
        data: Optional[dict[str, Any]],
        attachments: Optional[list[Attachment]],
        task_id: Optional[str],
        context_id: Optional[str],
    ) -> dict[str, Any]:
        msg = to_a2a_message(
            role="user",
            message_id=str(uuid.uuid4()),
            context_id=context_id,
            task_id=task_id,
            text=text,
            data=data,
            attachments=attachments,
        )
        sign_message(
            msg,
            self.keypair,
            self.entity_id,
            fqan=self.fqan,
            developer_proof=self.developer_proof,
        )
        return msg


# -----------------------------------------------------------------------------
# Card discovery
# -----------------------------------------------------------------------------


A2ATransport = Literal["JSONRPC", "HTTP+JSON"]


@dataclass
class ResolvedTransport:
    transport: A2ATransport
    url: str


def _normalize_transport(t: str) -> Optional[A2ATransport]:
    up = (t or "").strip().upper()
    if up in ("JSONRPC", "JSON-RPC", ""):
        return "JSONRPC"
    if up in ("HTTP+JSON", "HTTPJSON", "HTTP_JSON"):
        return "HTTP+JSON"
    return None


def resolve_transport_from_card(
    card: dict[str, Any],
    prefer: str = "auto",
) -> ResolvedTransport:
    """Pick {transport, url} from a card payload.

    `prefer="auto"` follows the card's `preferredTransport`. Pass
    "JSONRPC" or "HTTP+JSON" to force a specific transport (raises if
    not advertised).
    """
    ifaces: list[ResolvedTransport] = []
    primary_url = card.get("url")
    card_preferred = _normalize_transport(card.get("preferredTransport") or "JSONRPC")
    if primary_url and card_preferred:
        ifaces.append(ResolvedTransport(transport=card_preferred, url=primary_url))
    for iface in card.get("additionalInterfaces") or []:
        t = _normalize_transport(iface.get("transport") or "")
        url = iface.get("url")
        if t and url:
            ifaces.append(ResolvedTransport(transport=t, url=url))
    if not ifaces:
        raise RuntimeError("no supported transport advertised on agent card")

    if prefer == "auto":
        return ifaces[0]
    norm = _normalize_transport(prefer)
    if norm is None:
        raise RuntimeError(f"unknown transport preference: {prefer!r}")
    for r in ifaces:
        if r.transport == norm:
            return r
    advertised = ", ".join(r.transport for r in ifaces)
    raise RuntimeError(
        f"transport {norm!r} not advertised; available: {advertised}"
    )


def resolve_transport(
    card_url: str,
    prefer: str = "auto",
    *,
    timeout: float = 10,
) -> ResolvedTransport:
    """Fetch the well-known agent card and resolve its transport+URL.

    Pass `prefer` to force a specific transport; default follows the card.
    """
    normalized = (
        card_url
        if card_url.endswith(".json")
        else f"{card_url.rstrip('/')}/.well-known/agent-card.json"
    )
    resp = requests.get(normalized, timeout=timeout)
    if not resp.ok:
        raise RuntimeError(f"agent-card fetch HTTP {resp.status_code}: {normalized}")
    return resolve_transport_from_card(resp.json(), prefer=prefer)


def resolve_a2a_endpoint(card_url: str, *, timeout: float = 10) -> str:
    """Back-compat wrapper. Returns the JSON-RPC endpoint URL only —
    callers that need transport awareness should use `resolve_transport`.
    """
    return resolve_transport(card_url, prefer="JSONRPC", timeout=timeout).url
