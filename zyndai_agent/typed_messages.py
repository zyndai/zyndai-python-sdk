"""
Typed message protocol for ZyndAI agent communication.

Pydantic v2 discriminated union replacing the untyped AgentMessage format.
Legacy messages (plain 'content' string without 'type' field) are auto-wrapped
as InvokeMessage with capability="legacy" for backwards compatibility.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from zyndai_agent.message import AgentMessage


def generate_id() -> str:
    return str(uuid.uuid4())


class MessageBase(BaseModel):
    """Shared fields for all typed messages."""

    message_id: str = Field(default_factory=generate_id)
    conversation_id: str = Field(default_factory=generate_id)
    sender_id: str
    sender_public_key: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    signature: str = ""


class InvokeMessage(MessageBase):
    type: Literal["invoke"] = "invoke"
    capability: str
    payload: dict = Field(default_factory=dict)
    max_budget_usd: float = 0.0
    timeout_seconds: int = 30
    in_reply_to: str | None = None


class InvokeResponse(MessageBase):
    type: Literal["invoke_response"] = "invoke_response"
    in_reply_to: str
    status: Literal["success", "error", "partial"]
    result: dict = Field(default_factory=dict)
    usage: dict | None = None


class StreamChunk(MessageBase):
    type: Literal["stream_chunk"] = "stream_chunk"
    in_reply_to: str
    chunk_index: int
    content: str
    is_final: bool = False


class TaskAssignment(MessageBase):
    type: Literal["task_assignment"] = "task_assignment"
    task_id: str
    description: str
    context: dict = Field(default_factory=dict)
    constraints: dict = Field(default_factory=dict)


class TaskNotification(MessageBase):
    type: Literal["task_notification"] = "task_notification"
    task_id: str
    in_reply_to: str
    status: Literal["started", "progress", "completed", "failed"]
    summary: str
    result: dict | None = None
    usage: dict | None = None


class ShutdownRequest(MessageBase):
    type: Literal["shutdown_request"] = "shutdown_request"
    reason: str | None = None


class ShutdownResponse(MessageBase):
    type: Literal["shutdown_response"] = "shutdown_response"
    in_reply_to: str
    approved: bool
    reason: str | None = None


TypedMessage = Annotated[
    Union[
        InvokeMessage,
        InvokeResponse,
        StreamChunk,
        TaskAssignment,
        TaskNotification,
        ShutdownRequest,
        ShutdownResponse,
    ],
    Field(discriminator="type"),
]

_typed_message_adapter = None


def _get_adapter():
    global _typed_message_adapter
    if _typed_message_adapter is None:
        from pydantic import TypeAdapter
        _typed_message_adapter = TypeAdapter(TypedMessage)
    return _typed_message_adapter


def parse_message(raw: dict) -> TypedMessage:
    """
    Parse raw dict into a typed message.

    If the dict has a 'content' field but no 'type' field, it's treated as a
    legacy AgentMessage and wrapped as InvokeMessage with capability="legacy".
    """
    if "type" not in raw and ("content" in raw or "prompt" in raw):
        content = raw.get("content", raw.get("prompt", ""))
        return InvokeMessage(
            message_id=raw.get("message_id", generate_id()),
            conversation_id=raw.get("conversation_id", generate_id()),
            sender_id=raw.get("sender_id", "unknown"),
            sender_public_key=raw.get("sender_public_key"),
            timestamp=datetime.now(timezone.utc),
            capability="legacy",
            payload={
                "content": content,
                "prompt": raw.get("prompt", content),
            },
            max_budget_usd=0.0,
            timeout_seconds=raw.get("timeout_seconds", 30),
            in_reply_to=raw.get("in_reply_to"),
        )

    return _get_adapter().validate_python(raw)


def typed_to_legacy(msg: TypedMessage) -> AgentMessage:
    """Convert a typed message back to the legacy AgentMessage format.

    Extracts human-readable content from typed message fields so that
    handlers using ``message.content`` always see the actual task/text
    rather than an empty string or a raw dict repr.
    """
    if isinstance(msg, InvokeMessage):
        # Priority: content > prompt > task > description > str(payload)
        content = (
            msg.payload.get("content")
            or msg.payload.get("prompt")
            or msg.payload.get("task")
            or msg.payload.get("description")
            or str(msg.payload)
        )
    elif isinstance(msg, InvokeResponse):
        content = str(msg.result)
    elif isinstance(msg, StreamChunk):
        content = msg.content
    elif isinstance(msg, TaskAssignment):
        content = msg.description
    elif isinstance(msg, TaskNotification):
        content = msg.summary
    else:
        content = msg.model_dump_json()

    return AgentMessage(
        content=content,
        sender_id=msg.sender_id,
        sender_public_key=msg.sender_public_key,
        message_id=msg.message_id,
        conversation_id=msg.conversation_id,
        message_type=msg.type if hasattr(msg, "type") else "query",
        in_reply_to=getattr(msg, "in_reply_to", None),
    )
