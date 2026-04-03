"""
Stateful conversation sessions for agent-to-agent communication.

AgentSession tracks messages, participants, shared context, and cumulative
cost for a single conversation_id. SessionManager provides lookup and
lifecycle management across all active sessions.
"""

from __future__ import annotations

import uuid
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

DEFAULT_MAX_SESSIONS = 1000
DEFAULT_MESSAGE_LIMIT = 500


@dataclass
class AgentSession:
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    conversation_id: str = ""
    participants: list[str] = field(default_factory=list)
    messages: list[Any] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    total_cost_usd: float = 0.0
    message_limit: int = DEFAULT_MESSAGE_LIMIT
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def add_message(self, msg: Any) -> None:
        with self._lock:
            self.messages.append(msg)
            if len(self.messages) > self.message_limit:
                self.messages = self.messages[-self.message_limit:]
            self.updated_at = datetime.now(timezone.utc)

            usage = getattr(msg, "usage", None)
            if isinstance(usage, dict):
                self.total_cost_usd += usage.get("cost_usd", 0.0)

    def get_history(self, limit: int = 50) -> list[Any]:
        with self._lock:
            return list(self.messages[-limit:])

    def to_dict(self) -> dict[str, Any]:
        def _serialize(msg: Any) -> dict:
            if hasattr(msg, "model_dump"):
                return msg.model_dump()
            if hasattr(msg, "to_dict"):
                return msg.to_dict()
            return {"content": str(msg)}

        with self._lock:
            return {
                "session_id": self.session_id,
                "conversation_id": self.conversation_id,
                "participants": list(self.participants),
                "messages": [_serialize(m) for m in self.messages],
                "context": dict(self.context),
                "total_cost_usd": self.total_cost_usd,
                "created_at": self.created_at.isoformat(),
                "updated_at": self.updated_at.isoformat(),
            }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentSession:
        return cls(
            session_id=data.get("session_id", str(uuid.uuid4())),
            conversation_id=data.get("conversation_id", ""),
            participants=data.get("participants", []),
            messages=data.get("messages", []),
            context=data.get("context", {}),
            total_cost_usd=data.get("total_cost_usd", 0.0),
            created_at=datetime.fromisoformat(data["created_at"]) if "created_at" in data else datetime.now(timezone.utc),
            updated_at=datetime.fromisoformat(data["updated_at"]) if "updated_at" in data else datetime.now(timezone.utc),
        )


class SessionManager:
    """Thread-safe manager for AgentSession instances with LRU eviction."""

    def __init__(self, max_sessions: int = DEFAULT_MAX_SESSIONS) -> None:
        self._sessions: OrderedDict[str, AgentSession] = OrderedDict()
        self._max_sessions = max_sessions
        self._lock = threading.Lock()

    def get_or_create(self, conversation_id: str, sender_id: str) -> AgentSession:
        with self._lock:
            if conversation_id in self._sessions:
                self._sessions.move_to_end(conversation_id)
                session = self._sessions[conversation_id]
                if sender_id not in session.participants:
                    session.participants.append(sender_id)
                return session

            session = AgentSession(
                conversation_id=conversation_id,
                participants=[sender_id],
            )
            self._sessions[conversation_id] = session

            while len(self._sessions) > self._max_sessions:
                self._sessions.popitem(last=False)

            return session

    def get_session(self, conversation_id: str) -> AgentSession | None:
        with self._lock:
            return self._sessions.get(conversation_id)

    @property
    def active_sessions(self) -> list[AgentSession]:
        with self._lock:
            return list(self._sessions.values())

    def close_session(self, conversation_id: str) -> None:
        with self._lock:
            self._sessions.pop(conversation_id, None)
