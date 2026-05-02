"""A2A wire types, v0.3 shapes.

Source: https://a2a-protocol.org/v0.3.0/specification/

Pydantic models for inbound validation; TypedDict / dataclass-style
helpers for outbound construction. Mirrors the Zod schemas in the
TypeScript SDK's `src/a2a/types.ts`.
"""

from typing import Any, Literal, Optional, Union
from pydantic import BaseModel, ConfigDict, Field

# -----------------------------------------------------------------------------
# Parts (TextPart | FilePart | DataPart)
# -----------------------------------------------------------------------------


class FileWithBytes(BaseModel):
    model_config = ConfigDict(extra="allow")

    bytes: str
    name: Optional[str] = None
    mimeType: Optional[str] = None


class FileWithUri(BaseModel):
    model_config = ConfigDict(extra="allow")

    uri: str
    name: Optional[str] = None
    mimeType: Optional[str] = None


# Pydantic v2 supports Union but discriminator on file works less cleanly
# than in Zod — we just allow either shape and the validator picks.
class TextPart(BaseModel):
    model_config = ConfigDict(extra="allow")

    kind: Literal["text"]
    text: str
    metadata: Optional[dict[str, Any]] = None


class FilePart(BaseModel):
    model_config = ConfigDict(extra="allow")

    kind: Literal["file"]
    file: Union[FileWithBytes, FileWithUri]
    metadata: Optional[dict[str, Any]] = None


class DataPart(BaseModel):
    model_config = ConfigDict(extra="allow")

    kind: Literal["data"]
    data: Any
    metadata: Optional[dict[str, Any]] = None


Part = Union[TextPart, FilePart, DataPart]


# -----------------------------------------------------------------------------
# Message
# -----------------------------------------------------------------------------


MessageRole = Literal["user", "agent"]


class Message(BaseModel):
    """A2A Message: one party's contribution to a Task or one-shot exchange."""

    model_config = ConfigDict(extra="allow")

    kind: Optional[Literal["message"]] = "message"
    messageId: str
    role: MessageRole
    parts: list[Part]
    taskId: Optional[str] = None
    contextId: Optional[str] = None
    referenceTaskIds: Optional[list[str]] = None
    metadata: Optional[dict[str, Any]] = None
    extensions: Optional[list[str]] = None


# -----------------------------------------------------------------------------
# Task lifecycle
# -----------------------------------------------------------------------------


TaskState = Literal[
    "submitted",
    "working",
    "input-required",
    "auth-required",
    "completed",
    "canceled",
    "failed",
    "rejected",
    "unknown",
]


TERMINAL_STATES: frozenset[str] = frozenset(
    ("completed", "canceled", "failed", "rejected")
)
INTERRUPTED_STATES: frozenset[str] = frozenset(("input-required", "auth-required"))


class TaskStatus(BaseModel):
    model_config = ConfigDict(extra="allow")

    state: TaskState
    message: Optional[Message] = None
    timestamp: Optional[str] = None


class Artifact(BaseModel):
    model_config = ConfigDict(extra="allow")

    artifactId: str
    name: Optional[str] = None
    description: Optional[str] = None
    parts: list[Part]
    metadata: Optional[dict[str, Any]] = None


class Task(BaseModel):
    """A2A Task object."""

    model_config = ConfigDict(extra="allow")

    kind: Optional[Literal["task"]] = "task"
    id: str
    contextId: str
    status: TaskStatus
    artifacts: Optional[list[Artifact]] = None
    history: Optional[list[Message]] = None
    metadata: Optional[dict[str, Any]] = None


# -----------------------------------------------------------------------------
# Streaming events
# -----------------------------------------------------------------------------


class TaskStatusUpdateEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    kind: Literal["status-update"]
    taskId: str
    contextId: str
    status: TaskStatus
    final: bool
    metadata: Optional[dict[str, Any]] = None


class TaskArtifactUpdateEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    kind: Literal["artifact-update"]
    taskId: str
    contextId: str
    artifact: Artifact
    append: Optional[bool] = None
    lastChunk: Optional[bool] = None
    metadata: Optional[dict[str, Any]] = None


# -----------------------------------------------------------------------------
# JSON-RPC envelope
# -----------------------------------------------------------------------------


class JsonRpcRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    jsonrpc: Literal["2.0"]
    id: Optional[Union[str, int]] = None
    method: str
    params: Optional[Any] = None


# JSON-RPC 2.0 standard error codes + A2A + Zynd-specific.
# Spec: https://a2a-protocol.org/v0.3.0/specification/#errors
RPC_PARSE_ERROR = -32700
RPC_INVALID_REQUEST = -32600
RPC_METHOD_NOT_FOUND = -32601
RPC_INVALID_PARAMS = -32602
RPC_INTERNAL_ERROR = -32603

A2A_TASK_NOT_FOUND = -32001
A2A_TASK_NOT_CANCELABLE = -32002
A2A_PUSH_NOTIFICATION_NOT_SUPPORTED = -32003
A2A_UNSUPPORTED_OPERATION = -32004
A2A_CONTENT_TYPE_NOT_SUPPORTED = -32005
A2A_INVALID_AGENT_RESPONSE = -32006
A2A_AUTHENTICATED_EXTENDED_CARD_NOT_CONFIGURED = -32007

# Zynd-specific
ZYND_AUTH_FAILED = -32100
ZYND_REPLAY_DETECTED = -32101
ZYND_AUTH_EXPIRED = -32102


# -----------------------------------------------------------------------------
# Method param shapes
# -----------------------------------------------------------------------------


class MessageSendParams(BaseModel):
    model_config = ConfigDict(extra="allow")

    message: Message
    configuration: Optional[dict[str, Any]] = None
    metadata: Optional[dict[str, Any]] = None


class TaskIdParams(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    metadata: Optional[dict[str, Any]] = None


class TaskQueryParams(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    historyLength: Optional[int] = None
    metadata: Optional[dict[str, Any]] = None


# -----------------------------------------------------------------------------
# Push notification config
# -----------------------------------------------------------------------------


class PushNotificationAuth(BaseModel):
    model_config = ConfigDict(extra="allow")

    schemes: list[str]
    credentials: Optional[str] = None


class PushNotificationConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    url: str
    token: Optional[str] = None
    authentication: Optional[PushNotificationAuth] = None


class TaskPushNotificationConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    taskId: str
    pushNotificationConfig: PushNotificationConfig


# -----------------------------------------------------------------------------
# x-zynd-auth (per-message Ed25519 authorization)
# -----------------------------------------------------------------------------


class DerivationProof(BaseModel):
    model_config = ConfigDict(extra="allow")

    developer_public_key: str
    entity_index: int
    developer_signature: str


class ZyndAuth(BaseModel):
    """Per-message authorization block embedded in
    `Message.metadata["x-zynd-auth"]`. The signature covers
    JCS(message) with this block's `signature` field blanked.
    See `auth.py` for sign/verify rules.
    """

    model_config = ConfigDict(extra="allow")

    v: Literal[1]
    entity_id: str
    fqan: Optional[str] = None
    public_key: str
    nonce: str
    issued_at: str
    expires_at: str
    signature: str
    developer_proof: Optional[DerivationProof] = None


ZYND_AUTH_KEY = "x-zynd-auth"
ZYND_AUTH_VERSION: Literal[1] = 1
ZYND_AUTH_DOMAIN_TAG = "ZYND-A2A-MSG-v1\n"
