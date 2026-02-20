"""
Tests for AgentMessage serialization, deserialization, and edge cases.
"""

import json
import pytest
from zyndai_agent.message import AgentMessage, MQTTMessage


class TestAgentMessageCreation:
    def test_basic_creation(self):
        msg = AgentMessage(content="hello", sender_id="agent-1")
        assert msg.content == "hello"
        assert msg.sender_id == "agent-1"
        assert msg.message_type == "query"
        assert msg.receiver_id is None
        assert msg.in_reply_to is None
        assert msg.metadata == {}
        assert msg.message_id is not None
        assert msg.conversation_id is not None
        assert msg.timestamp > 0

    def test_full_creation(self):
        msg = AgentMessage(
            content="response text",
            sender_id="agent-1",
            sender_did={"issuer": "did:test"},
            receiver_id="agent-2",
            message_type="response",
            message_id="msg-123",
            conversation_id="conv-456",
            in_reply_to="msg-000",
            metadata={"key": "value"},
        )
        assert msg.content == "response text"
        assert msg.sender_did == {"issuer": "did:test"}
        assert msg.receiver_id == "agent-2"
        assert msg.message_type == "response"
        assert msg.message_id == "msg-123"
        assert msg.conversation_id == "conv-456"
        assert msg.in_reply_to == "msg-000"
        assert msg.metadata == {"key": "value"}

    def test_auto_generated_ids_are_unique(self):
        msg1 = AgentMessage(content="a", sender_id="s")
        msg2 = AgentMessage(content="b", sender_id="s")
        assert msg1.message_id != msg2.message_id
        assert msg1.conversation_id != msg2.conversation_id


class TestAgentMessageSerialization:
    def test_to_dict(self):
        msg = AgentMessage(
            content="test", sender_id="agent-1", message_id="m1", conversation_id="c1"
        )
        d = msg.to_dict()
        assert d["content"] == "test"
        assert d["prompt"] == "test"  # both content and prompt are set
        assert d["sender_id"] == "agent-1"
        assert d["message_id"] == "m1"
        assert d["conversation_id"] == "c1"
        assert "timestamp" in d

    def test_to_json(self):
        msg = AgentMessage(content="test", sender_id="agent-1")
        json_str = msg.to_json()
        parsed = json.loads(json_str)
        assert parsed["content"] == "test"
        assert parsed["sender_id"] == "agent-1"

    def test_to_json_is_valid_json(self):
        msg = AgentMessage(
            content='Message with "quotes" and \n newlines',
            sender_id="agent-1",
            metadata={"nested": {"key": "value"}},
        )
        json_str = msg.to_json()
        parsed = json.loads(json_str)  # Should not raise
        assert parsed["content"] == 'Message with "quotes" and \n newlines'


class TestAgentMessageDeserialization:
    def test_from_dict_with_content(self):
        data = {
            "content": "hello world",
            "sender_id": "agent-1",
            "message_type": "query",
            "message_id": "msg-1",
            "conversation_id": "conv-1",
        }
        msg = AgentMessage.from_dict(data)
        assert msg.content == "hello world"
        assert msg.sender_id == "agent-1"
        assert msg.message_id == "msg-1"

    def test_from_dict_with_prompt_field(self):
        """The 'prompt' field should be used as content when 'content' is absent."""
        data = {
            "prompt": "prompt content",
            "sender_id": "agent-1",
        }
        msg = AgentMessage.from_dict(data)
        assert msg.content == "prompt content"

    def test_from_dict_prefers_prompt_over_content(self):
        """When both prompt and content exist, prompt takes priority (from_dict logic)."""
        data = {
            "prompt": "prompt value",
            "content": "content value",
            "sender_id": "agent-1",
        }
        msg = AgentMessage.from_dict(data)
        # from_dict uses: data.get("prompt", data.get("content", ""))
        assert msg.content == "prompt value"

    def test_from_dict_defaults(self):
        msg = AgentMessage.from_dict({})
        assert msg.content == ""
        assert msg.sender_id == "unknown"
        assert msg.message_type == "query"
        assert msg.metadata == {}

    def test_from_json_valid(self):
        original = AgentMessage(content="roundtrip", sender_id="agent-1")
        json_str = original.to_json()
        restored = AgentMessage.from_json(json_str)
        assert restored.content == "roundtrip"
        assert restored.sender_id == "agent-1"

    def test_from_json_invalid_falls_back_to_raw(self):
        msg = AgentMessage.from_json("this is not json")
        assert msg.content == "this is not json"
        assert msg.sender_id == "unknown"
        assert msg.message_type == "raw"

    def test_roundtrip_dict(self):
        original = AgentMessage(
            content="test",
            sender_id="s1",
            receiver_id="r1",
            message_type="response",
            metadata={"k": "v"},
        )
        d = original.to_dict()
        restored = AgentMessage.from_dict(d)
        assert restored.sender_id == original.sender_id
        assert restored.receiver_id == original.receiver_id
        assert restored.message_type == original.message_type


class TestMQTTMessageAlias:
    def test_mqtt_message_is_agent_message(self):
        assert MQTTMessage is AgentMessage

    def test_mqtt_message_works(self):
        msg = MQTTMessage(content="mqtt test", sender_id="agent-1")
        assert isinstance(msg, AgentMessage)
        assert msg.content == "mqtt test"
