"""Adapter between A2A wire types (dicts) and the SDK's high-level
AgentMessage. Mirrors `zyndai-ts-sdk/src/a2a/adapter.ts`.

Inbound:  A2A Message dict → AgentMessage + parsed payload + Attachments
Outbound: AgentMessage / handler output → A2A Message dict (parts)

The handler-facing AgentMessage stays roughly what it was — only the
wire shape changes. Existing handler code that reads `message.content`
still works.
"""

import json
from dataclasses import dataclass
from typing import Any, Optional, Type

from pydantic import BaseModel

from zyndai_agent.message import AgentMessage
from zyndai_agent.payload import AgentPayload


# -----------------------------------------------------------------------------
# Attachment — handler-facing representation
# -----------------------------------------------------------------------------


@dataclass
class Attachment:
    """A file/image/audio/video attached to an A2A message. Mirrors the TS
    SDK's Attachment shape and the existing Pydantic Attachment model in
    payload.py — they're isomorphic on the wire.
    """

    filename: Optional[str] = None
    mime_type: Optional[str] = None
    data: Optional[str] = None  # base64-encoded bytes (inline)
    url: Optional[str] = None  # remote reference (FileWithUri)


def _file_to_attachment(file_dict: dict[str, Any]) -> Attachment:
    att = Attachment()
    if file_dict.get("name"):
        att.filename = file_dict["name"]
    if file_dict.get("mimeType"):
        att.mime_type = file_dict["mimeType"]
    if "bytes" in file_dict:
        att.data = file_dict["bytes"]
    if "uri" in file_dict:
        att.url = file_dict["uri"]
    return att


def _attachment_to_file(att: Attachment) -> dict[str, Any]:
    base: dict[str, Any] = {}
    if att.filename is not None:
        base["name"] = att.filename
    if att.mime_type is not None:
        base["mimeType"] = att.mime_type
    if att.data is not None:
        return {**base, "bytes": att.data}
    if att.url is not None:
        return {**base, "uri": att.url}
    raise ValueError("attachment_to_file: Attachment has neither `data` nor `url`")


# -----------------------------------------------------------------------------
# Inbound: A2A Message dict → SDK shapes
# -----------------------------------------------------------------------------


@dataclass
class InboundMessage:
    """The fully-parsed inbound: high-level AgentMessage + structured
    payload (validated against the agent's payload model when supplied)
    + raw attachments + sender role flag.
    """

    message: AgentMessage
    payload: dict[str, Any]
    attachments: list[Attachment]
    from_agent: bool


def from_a2a_message(
    message: dict[str, Any],
    payload_model: Optional[Type[BaseModel]] = None,
) -> InboundMessage:
    """Parse an inbound A2A Message dict into an InboundMessage.

    Conversion rules:
      - All TextParts are concatenated (newline-joined) into the legacy
        `content` field, also exposed as `prompt` for backward compat
        with templates whose RequestPayload schema uses
        `prompt: str` (mirrors the TS adapter's same alias).
      - All DataParts are merged into the payload object. Later parts
        win on key collision.
      - All FileParts become Attachments. Handlers can read them via
        `attachments` or via the validated payload model if the model
        declared a list-of-Attachment field.
    """
    parts: list[dict[str, Any]] = message.get("parts", []) or []
    texts: list[str] = []
    data_merge: dict[str, Any] = {}
    attachments: list[Attachment] = []

    for part in parts:
        kind = part.get("kind")
        if kind == "text" and isinstance(part.get("text"), str):
            texts.append(part["text"])
        elif kind == "data" and isinstance(part.get("data"), dict):
            data_merge.update(part["data"])
        elif kind == "file" and isinstance(part.get("file"), dict):
            attachments.append(_file_to_attachment(part["file"]))

    content = "\n".join(texts).strip()

    # Compose the payload dict the handler will see. Expose both
    # `content` (canonical) and `prompt` (alias) so legacy schemas
    # declared as `prompt: str` continue to validate.
    text_value = (
        content
        or data_merge.get("content")
        or data_merge.get("prompt")
        or ""
    )
    metadata = message.get("metadata") or {}
    auth = metadata.get("x-zynd-auth") or {}

    payload_dict: dict[str, Any] = {
        **data_merge,
        "content": text_value,
        "prompt": text_value,
        "attachments": [a.__dict__ for a in attachments],
        "sender_id": auth.get("entity_id", "unknown"),
        "message_id": message.get("messageId"),
        "conversation_id": message.get("contextId"),
        "in_reply_to": message.get("taskId"),
    }

    # Validate against the payload model when supplied. Errors propagate.
    if payload_model is not None:
        validated = payload_model.model_validate(payload_dict).model_dump()
    else:
        validated = payload_dict

    agent_msg = AgentMessage(
        content=content,
        sender_id=auth.get("entity_id", "unknown"),
        sender_public_key=auth.get("public_key"),
        message_id=message.get("messageId"),
        conversation_id=message.get("contextId") or message.get("messageId"),
        metadata=metadata,
    )

    return InboundMessage(
        message=agent_msg,
        payload=validated,
        attachments=attachments,
        from_agent=message.get("role") == "agent",
    )


# -----------------------------------------------------------------------------
# Outbound: build an A2A Message dict
# -----------------------------------------------------------------------------


def to_a2a_message(
    *,
    role: str,
    message_id: str,
    context_id: Optional[str] = None,
    task_id: Optional[str] = None,
    text: Optional[str] = None,
    data: Optional[dict[str, Any]] = None,
    attachments: Optional[list[Attachment]] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build an A2A Message dict from raw text + optional structured data
    + attachments. Parts emitted in order: data first, then text, then
    files — keeps LLM-context-relevant content (data, text) up front.
    """
    parts: list[dict[str, Any]] = []

    if data:
        parts.append({"kind": "data", "data": data})

    if text:
        parts.append({"kind": "text", "text": text})

    if attachments:
        for att in attachments:
            parts.append({"kind": "file", "file": _attachment_to_file(att)})

    msg: dict[str, Any] = {
        "kind": "message",
        "messageId": message_id,
        "role": role,
        "parts": parts,
    }
    if context_id:
        msg["contextId"] = context_id
    if task_id:
        msg["taskId"] = task_id
    if metadata:
        msg["metadata"] = metadata

    return msg


# -----------------------------------------------------------------------------
# Coerce handler return values into (text, data, attachments) tuple
# -----------------------------------------------------------------------------


def coerce_handler_output(value: Any) -> dict[str, Any]:
    """Normalize a handler return value into a dict with optional
    `text`, `data`, `attachments` keys for the outbound builder.

    Rules (match TS coerceHandlerOutput):
      - None    → {"text": ""}
      - str     → {"text": ...}
      - dict    → look for "text"/"content"/"data"/"attachments"; if
                  none found, treat the whole dict as data
      - other   → str(value) as text
    """
    if value is None:
        return {"text": ""}
    if isinstance(value, str):
        return {"text": value}
    if isinstance(value, BaseModel):
        return coerce_handler_output(value.model_dump())
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        if isinstance(value.get("text"), str):
            out["text"] = value["text"]
        elif isinstance(value.get("content"), str):
            out["text"] = value["content"]

        if isinstance(value.get("attachments"), list):
            out["attachments"] = value["attachments"]

        if isinstance(value.get("data"), dict):
            out["data"] = value["data"]
        elif "text" not in out and "attachments" not in out:
            # Whole dict is the data payload.
            out["data"] = value
        return out
    return {"text": str(value)}


# -----------------------------------------------------------------------------
# Read agent reply text out of a Task object
# -----------------------------------------------------------------------------


def task_reply_text(task: dict[str, Any]) -> str:
    """Extract the agent's reply text from a completed Task dict.

    Reads in priority order:
      1. task.artifacts[].parts        — where completed-task replies live;
                                          handler returns end up here.
      2. task.status.message.parts     — when the agent attached a message
                                          to a non-terminal status update.
      3. (last fallback) task.history  — for input-required loopbacks.

    **Do NOT read task.history[last] directly** to get the response —
    history contains the conversation log including the caller's own
    outbound message, so naive `history[-1]` returns the caller's input
    back at it. That misread caused infinite tool loops in early
    LangChain agents on the TS side.
    """
    artifacts = task.get("artifacts") or []
    from_artifacts = "\n".join(
        s for s in (parts_to_reply_text(a.get("parts") or []) for a in artifacts) if s
    ).strip()
    if from_artifacts:
        return from_artifacts

    status = task.get("status") or {}
    status_msg = status.get("message") or {}
    from_status = parts_to_reply_text(status_msg.get("parts") or [])
    if from_status:
        return from_status

    history = task.get("history") or []
    if history:
        last = history[-1] or {}
        from_history = parts_to_reply_text(last.get("parts") or [])
        if from_history:
            return from_history

    state = (task.get("status") or {}).get("state", "unknown")
    return f"(task {state})"


def parts_to_reply_text(parts: list[dict[str, Any]]) -> str:
    """Walk a Parts array and join into a single reply string. Internal
    helper for task_reply_text — exported for advanced callers who
    already have a Parts array (e.g. from a status-update event).
    """
    chunks: list[str] = []
    for raw in parts:
        kind = raw.get("kind")
        if kind == "text" and isinstance(raw.get("text"), str):
            chunks.append(raw["text"])
        elif kind == "data" and isinstance(raw.get("data"), dict):
            d = raw["data"]
            if isinstance(d.get("response"), str):
                chunks.append(d["response"])
            elif isinstance(d.get("text"), str):
                chunks.append(d["text"])
            else:
                chunks.append(json.dumps(d, ensure_ascii=False))
    return "\n".join(chunks).strip()
