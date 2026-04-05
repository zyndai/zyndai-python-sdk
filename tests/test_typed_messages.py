"""Tests for the typed message protocol."""

import pytest
from datetime import datetime, timezone
from pydantic import ValidationError

from zyndai_agent.typed_messages import (
    InvokeMessage,
    InvokeResponse,
    StreamChunk,
    TaskAssignment,
    TaskNotification,
    ShutdownRequest,
    ShutdownResponse,
    parse_message,
    typed_to_legacy,
    generate_id,
)


class TestParseMessage:
    def test_parse_invoke_message(self):
        raw = {
            "type": "invoke",
            "message_id": "msg-1",
            "conversation_id": "conv-1",
            "sender_id": "agent-a",
            "capability": "translate",
            "payload": {"text": "hello", "language": "French"},
        }
        msg = parse_message(raw)
        assert isinstance(msg, InvokeMessage)
        assert msg.capability == "translate"
        assert msg.payload["text"] == "hello"
        assert msg.max_budget_usd == 0.0

    def test_parse_invoke_response(self):
        raw = {
            "type": "invoke_response",
            "message_id": "msg-2",
            "conversation_id": "conv-1",
            "sender_id": "agent-b",
            "in_reply_to": "msg-1",
            "status": "success",
            "result": {"translated": "bonjour"},
        }
        msg = parse_message(raw)
        assert isinstance(msg, InvokeResponse)
        assert msg.status == "success"
        assert msg.result["translated"] == "bonjour"

    def test_parse_stream_chunk(self):
        raw = {
            "type": "stream_chunk",
            "message_id": "msg-3",
            "conversation_id": "conv-1",
            "sender_id": "agent-b",
            "in_reply_to": "msg-1",
            "chunk_index": 0,
            "content": "partial result",
            "is_final": False,
        }
        msg = parse_message(raw)
        assert isinstance(msg, StreamChunk)
        assert msg.chunk_index == 0
        assert not msg.is_final

    def test_parse_task_assignment(self):
        raw = {
            "type": "task_assignment",
            "message_id": "msg-4",
            "conversation_id": "conv-1",
            "sender_id": "coordinator",
            "task_id": "task-1",
            "description": "search for papers",
            "context": {"topic": "AI"},
            "constraints": {"timeout": 30},
        }
        msg = parse_message(raw)
        assert isinstance(msg, TaskAssignment)
        assert msg.task_id == "task-1"

    def test_parse_task_notification(self):
        raw = {
            "type": "task_notification",
            "message_id": "msg-5",
            "conversation_id": "conv-1",
            "sender_id": "worker-1",
            "task_id": "task-1",
            "in_reply_to": "msg-4",
            "status": "completed",
            "summary": "Found 5 papers",
            "result": {"papers": 5},
            "usage": {"tokens": 1000, "cost_usd": 0.01},
        }
        msg = parse_message(raw)
        assert isinstance(msg, TaskNotification)
        assert msg.status == "completed"
        assert msg.usage["cost_usd"] == 0.01

    def test_parse_shutdown_request(self):
        raw = {
            "type": "shutdown_request",
            "message_id": "msg-6",
            "conversation_id": "conv-1",
            "sender_id": "coordinator",
            "reason": "task complete",
        }
        msg = parse_message(raw)
        assert isinstance(msg, ShutdownRequest)
        assert msg.reason == "task complete"

    def test_parse_shutdown_response(self):
        raw = {
            "type": "shutdown_response",
            "message_id": "msg-7",
            "conversation_id": "conv-1",
            "sender_id": "worker-1",
            "in_reply_to": "msg-6",
            "approved": True,
        }
        msg = parse_message(raw)
        assert isinstance(msg, ShutdownResponse)
        assert msg.approved is True


class TestLegacyCompat:
    def test_legacy_content_message_wraps_as_invoke(self):
        raw = {
            "content": "translate hello to French",
            "sender_id": "agent-a",
            "message_type": "query",
        }
        msg = parse_message(raw)
        assert isinstance(msg, InvokeMessage)
        assert msg.capability == "legacy"
        assert msg.payload["content"] == "translate hello to French"

    def test_legacy_prompt_field(self):
        raw = {"prompt": "do something", "sender_id": "test"}
        msg = parse_message(raw)
        assert isinstance(msg, InvokeMessage)
        assert msg.payload["content"] == "do something"

    def test_legacy_preserves_message_id(self):
        raw = {
            "content": "hello",
            "sender_id": "test",
            "message_id": "custom-id",
            "conversation_id": "conv-99",
        }
        msg = parse_message(raw)
        assert msg.message_id == "custom-id"
        assert msg.conversation_id == "conv-99"


class TestInvalidMessages:
    def test_missing_type_and_content_raises(self):
        with pytest.raises((ValidationError, KeyError)):
            parse_message({"sender_id": "test", "type": "bogus"})

    def test_invalid_status_raises(self):
        with pytest.raises(ValidationError):
            parse_message({
                "type": "invoke_response",
                "message_id": "x",
                "conversation_id": "y",
                "sender_id": "z",
                "in_reply_to": "a",
                "status": "bogus_status",
                "result": {},
            })


class TestTypedToLegacy:
    def test_invoke_to_legacy(self):
        msg = InvokeMessage(
            sender_id="agent-a",
            capability="translate",
            payload={"content": "hello world"},
        )
        legacy = typed_to_legacy(msg)
        assert legacy.content == "hello world"
        assert legacy.sender_id == "agent-a"
        assert legacy.message_type == "invoke"

    def test_response_to_legacy(self):
        msg = InvokeResponse(
            sender_id="agent-b",
            in_reply_to="msg-1",
            status="success",
            result={"answer": 42},
        )
        legacy = typed_to_legacy(msg)
        assert "42" in legacy.content
        assert legacy.message_type == "invoke_response"


class TestRoundTrip:
    def test_invoke_serialize_deserialize(self):
        original = InvokeMessage(
            sender_id="agent-a",
            capability="search",
            payload={"query": "test"},
            max_budget_usd=0.5,
        )
        raw = original.model_dump(mode="json")
        restored = parse_message(raw)
        assert isinstance(restored, InvokeMessage)
        assert restored.capability == original.capability
        assert restored.payload == original.payload
        assert restored.message_id == original.message_id


class TestGenerateId:
    def test_unique(self):
        ids = {generate_id() for _ in range(100)}
        assert len(ids) == 100

    def test_format(self):
        id_ = generate_id()
        assert len(id_) == 36  # UUID4 with hyphens
