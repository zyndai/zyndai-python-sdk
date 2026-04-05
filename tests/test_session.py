"""Tests for agent sessions and session manager."""

import pytest
from datetime import datetime, timezone

from zyndai_agent.session import AgentSession, SessionManager
from zyndai_agent.typed_messages import InvokeMessage, InvokeResponse


class TestAgentSession:
    def test_create_session(self):
        s = AgentSession(conversation_id="conv-1", participants=["agent-a"])
        assert s.conversation_id == "conv-1"
        assert s.total_cost_usd == 0.0
        assert len(s.messages) == 0

    def test_add_message(self):
        s = AgentSession(conversation_id="conv-1")
        msg = InvokeMessage(sender_id="a", capability="test", payload={})
        s.add_message(msg)
        assert len(s.messages) == 1
        assert s.messages[0] is msg

    def test_add_message_tracks_cost(self):
        s = AgentSession(conversation_id="conv-1")
        msg = InvokeResponse(
            sender_id="b",
            in_reply_to="x",
            status="success",
            result={},
            usage={"cost_usd": 0.05, "tokens": 500},
        )
        s.add_message(msg)
        assert s.total_cost_usd == pytest.approx(0.05)

    def test_add_message_no_usage(self):
        s = AgentSession(conversation_id="conv-1")
        msg = InvokeMessage(sender_id="a", capability="test", payload={})
        s.add_message(msg)
        assert s.total_cost_usd == 0.0

    def test_get_history_default(self):
        s = AgentSession(conversation_id="conv-1")
        for i in range(100):
            s.add_message(InvokeMessage(sender_id="a", capability=f"cap-{i}", payload={}))
        history = s.get_history(limit=10)
        assert len(history) == 10
        assert history[0].capability == "cap-90"

    def test_to_dict_from_dict(self):
        s = AgentSession(conversation_id="conv-1", participants=["a", "b"])
        msg = InvokeMessage(sender_id="a", capability="test", payload={"k": "v"})
        s.add_message(msg)

        d = s.to_dict()
        assert d["conversation_id"] == "conv-1"
        assert len(d["messages"]) == 1
        assert d["messages"][0]["capability"] == "test"

        restored = AgentSession.from_dict(d)
        assert restored.conversation_id == "conv-1"
        assert restored.participants == ["a", "b"]

    def test_updated_at_changes(self):
        s = AgentSession(conversation_id="conv-1")
        t0 = s.updated_at
        import time
        time.sleep(0.01)
        s.add_message(InvokeMessage(sender_id="a", capability="x", payload={}))
        assert s.updated_at > t0


class TestSessionManager:
    def test_get_or_create_new(self):
        mgr = SessionManager()
        s = mgr.get_or_create("conv-1", "agent-a")
        assert s.conversation_id == "conv-1"
        assert "agent-a" in s.participants

    def test_get_or_create_existing(self):
        mgr = SessionManager()
        s1 = mgr.get_or_create("conv-1", "agent-a")
        s2 = mgr.get_or_create("conv-1", "agent-b")
        assert s1 is s2
        assert "agent-a" in s1.participants
        assert "agent-b" in s1.participants

    def test_get_session_missing(self):
        mgr = SessionManager()
        assert mgr.get_session("nonexistent") is None

    def test_active_sessions(self):
        mgr = SessionManager()
        mgr.get_or_create("conv-1", "a")
        mgr.get_or_create("conv-2", "b")
        assert len(mgr.active_sessions) == 2

    def test_close_session(self):
        mgr = SessionManager()
        mgr.get_or_create("conv-1", "a")
        mgr.close_session("conv-1")
        assert mgr.get_session("conv-1") is None
        assert len(mgr.active_sessions) == 0

    def test_close_nonexistent_is_noop(self):
        mgr = SessionManager()
        mgr.close_session("nonexistent")
