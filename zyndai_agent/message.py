import time
import json
import uuid
import logging
from typing import Optional, Dict, Any, Type

from zyndai_agent.payload import AgentPayload, Attachment

logger = logging.getLogger(__name__)

class AgentMessage:
    """
    Structured message format for agent communication.

    This class provides a standardized way to format, serialize, and deserialize
    messages exchanged between agents, with support for conversation threading,
    message types, and metadata.

    Protocol-agnostic: Can be used with MQTT, HTTP webhooks, or other transports.
    """

    def __init__(
        self,
        content: str,
        sender_id: str,
        sender_did: dict = None,
        sender_public_key: Optional[str] = None,
        receiver_id: Optional[str] = None,
        message_type: str = "query",
        message_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        in_reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        attachments: Optional[list[Attachment]] = None,
        payload: Optional[AgentPayload] = None,
    ):
        """
        Initialize a new agent message.

        Args:
            content: The main message content
            sender_id: Identifier for the message sender
            sender_did: DID credential of the sender (deprecated, kept for backward compat)
            sender_public_key: Ed25519 public key string of sender (e.g., "ed25519:<b64>")
            receiver_id: Identifier for the intended recipient (None for broadcasts)
            message_type: Type categorization ("query", "response", "broadcast", "system")
            message_id: Unique identifier for this message (auto-generated if None)
            conversation_id: ID grouping related messages (auto-generated if None)
            in_reply_to: ID of the message this is responding to (None if not a reply)
            metadata: Additional contextual information
        """
        self.content = content
        self.sender_id = sender_id
        self.receiver_id = receiver_id
        self.sender_did = sender_did
        self.sender_public_key = sender_public_key
        self.message_type = message_type
        self.message_id = message_id or str(uuid.uuid4())
        self.conversation_id = conversation_id or str(uuid.uuid4())
        self.in_reply_to = in_reply_to
        self.metadata = metadata or {}
        self.attachments = attachments or []
        # The validated Pydantic payload — handlers should access custom
        # fields (declared on their RequestPayload subclass) through this
        # attribute, e.g. `message.payload.pdfs`. None only for messages
        # constructed directly via the legacy constructor path.
        self.payload = payload
        self.timestamp = time.time()

    def to_dict(self) -> Dict[str, Any]:
        """Convert message to dictionary format."""
        d = {
            "content": self.content,
            "prompt": self.content,
            "sender_id": self.sender_id,
            "sender_did": self.sender_did,
            "sender_public_key": self.sender_public_key,
            "receiver_id": self.receiver_id,
            "message_type": self.message_type,
            "message_id": self.message_id,
            "conversation_id": self.conversation_id,
            "in_reply_to": self.in_reply_to,
            "metadata": self.metadata,
            "attachments": [a.model_dump(exclude_none=True) for a in self.attachments],
            "timestamp": self.timestamp
        }
        return d

    def to_json(self) -> str:
        """Convert message to JSON string for transmission."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(
        cls,
        data: Dict[str, Any],
        payload_model: Type[AgentPayload] = AgentPayload,
    ) -> 'AgentMessage':
        """Create message object from dictionary data.

        Validates incoming data against `payload_model` (the default
        `AgentPayload` schema, or a developer-supplied subclass).
        """
        payload = payload_model.model_validate(data or {})
        return cls(
            content=payload.content,
            sender_id=payload.sender_id,
            sender_did=payload.sender_did,
            sender_public_key=payload.sender_public_key,
            receiver_id=payload.receiver_id,
            message_type=payload.message_type,
            message_id=payload.message_id,
            conversation_id=payload.conversation_id,
            in_reply_to=payload.in_reply_to,
            metadata=payload.metadata,
            # Only surface attachments when the payload model explicitly
            # declared the field — extras carrying raw dicts shouldn't leak
            # into handlers as if they'd been validated.
            attachments=(
                payload.attachments
                if "attachments" in getattr(payload_model, "model_fields", {})
                else []
            ),
            payload=payload,
        )

    @classmethod
    def from_json(cls, json_str: str) -> 'AgentMessage':
        """
        Create message object from JSON string.

        Handles both valid JSON and fallback for plain text messages.
        """
        try:
            data = json.loads(json_str)
            return cls.from_dict(data)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse message as JSON: {e}")
            return cls(
                content=json_str,
                sender_id="unknown",
                message_type="raw"
            )


# Backward compatibility alias
MQTTMessage = AgentMessage
