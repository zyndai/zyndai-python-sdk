"""Outbound A2A client.

Mirrors `zyndai-ts-sdk/src/a2a/client.ts`. Sync API since Python users
typically integrate via Flask handlers / sync code paths. Streaming is
implemented as a generator yielding StreamEvent dicts.
"""

import json
import uuid
from typing import Any, Generator, Optional

import requests

from zyndai_agent.a2a.adapter import (
    Attachment,
    task_reply_text,
    to_a2a_message,
)
from zyndai_agent.a2a.auth import sign_message
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
    ) -> None:
        self.keypair = keypair
        self.entity_id = entity_id
        self.fqan = fqan
        self.developer_proof = developer_proof
        self.timeout = timeout

    # -------------------------------------------------------------------------
    # Sync — single request/response, returns final Task dict.
    # -------------------------------------------------------------------------

    def sync(
        self,
        url: str,
        *,
        text: Optional[str] = None,
        data: Optional[dict[str, Any]] = None,
        attachments: Optional[list[Attachment]] = None,
        task_id: Optional[str] = None,
        context_id: Optional[str] = None,
        blocking: bool = True,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """Send `message/send` and return the final Task dict."""
        message = self._build_message(
            text=text,
            data=data,
            attachments=attachments,
            task_id=task_id,
            context_id=context_id,
        )
        rpc = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "message/send",
            "params": {
                "message": message,
                "configuration": {"blocking": blocking},
            },
        }
        resp = requests.post(
            url,
            json=rpc,
            timeout=timeout or self.timeout,
            headers={"Content-Type": "application/json"},
        )
        if not resp.ok:
            raise RuntimeError(f"A2A sync HTTP {resp.status_code}: {resp.text}")

        body = resp.json()
        if "error" in body:
            raise A2AError(body)

        result = body.get("result")
        if isinstance(result, dict) and result.get("kind") == "task":
            return result
        # Server returned a bare Message — wrap in synthetic Task.
        return {
            "kind": "task",
            "id": str(uuid.uuid4()),
            "contextId": context_id or str(uuid.uuid4()),
            "status": {"state": "completed"},
            "history": [result] if result else [],
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
        message = self._build_message(
            text=text,
            data=data,
            attachments=attachments,
            task_id=task_id,
            context_id=context_id,
        )
        rpc = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "message/stream",
            "params": {"message": message},
        }
        with requests.post(
            url,
            json=rpc,
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
    ) -> dict[str, Any]:
        """Resolve an agent card URL to its A2A endpoint, then sync()."""
        endpoint = resolve_a2a_endpoint(card_or_base_url, timeout=timeout or 10)
        return self.sync(
            endpoint,
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


def resolve_a2a_endpoint(card_url: str, *, timeout: float = 10) -> str:
    """Fetch the agent card and return its primary A2A JSON-RPC URL.

    Accepts:
      - direct card URL ending in .json (fetched verbatim)
      - base URL (we append /.well-known/agent-card.json)
    """
    normalized = (
        card_url
        if card_url.endswith(".json")
        else f"{card_url.rstrip('/')}/.well-known/agent-card.json"
    )
    resp = requests.get(normalized, timeout=timeout)
    if not resp.ok:
        raise RuntimeError(f"agent-card fetch HTTP {resp.status_code}: {normalized}")
    card = resp.json()

    preferred = (card.get("preferredTransport") or "JSONRPC").upper()
    primary = card.get("url")
    if primary and preferred in ("JSONRPC", ""):
        return primary
    for iface in card.get("additionalInterfaces") or []:
        if (iface.get("transport") or "").upper() == "JSONRPC":
            return iface["url"]
    if primary:
        return primary
    raise RuntimeError("no JSON-RPC endpoint advertised on agent card")
