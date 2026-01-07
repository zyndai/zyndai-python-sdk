import time
import json
import uuid
import logging
from typing import Optional, Dict, Any

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
        receiver_id: Optional[str] = None,
        message_type: str = "query",
        message_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        in_reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize a new agent message.

        Args:
            content: The main message content
            sender_id: Identifier for the message sender
            sender_did: DID credential of the sender
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
        self.message_type = message_type
        self.message_id = message_id or str(uuid.uuid4())
        self.conversation_id = conversation_id or str(uuid.uuid4())
        self.in_reply_to = in_reply_to
        self.metadata = metadata or {}
        self.timestamp = time.time()

    def to_dict(self) -> Dict[str, Any]:
        """Convert message to dictionary format."""
        return {
            "content": self.content,
            "sender_id": self.sender_id,
            "sender_did": self.sender_did,
            "receiver_id": self.receiver_id,
            "message_type": self.message_type,
            "message_id": self.message_id,
            "conversation_id": self.conversation_id,
            "in_reply_to": self.in_reply_to,
            "metadata": self.metadata,
            "timestamp": self.timestamp
        }

    def to_json(self) -> str:
        """Convert message to JSON string for transmission."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AgentMessage':
        """Create message object from dictionary data."""
        return cls(
            content=data.get("content", ""),
            sender_id=data.get("sender_id", "unknown"),
            sender_did=data.get("sender_did", "unknown"),
            receiver_id=data.get("receiver_id"),
            message_type=data.get("message_type", "query"),
            message_id=data.get("message_id"),
            conversation_id=data.get("conversation_id"),
            in_reply_to=data.get("in_reply_to"),
            metadata=data.get("metadata", {})
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
