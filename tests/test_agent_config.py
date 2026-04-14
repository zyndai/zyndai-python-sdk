"""
Tests for AgentConfig (Pydantic model) validation and defaults.
"""

import pytest
from zyndai_agent.agent import AgentConfig


class TestAgentConfigDefaults:
    def test_all_defaults(self):
        config = AgentConfig()
        assert config.name == ""
        assert config.description == ""
        assert config.capabilities is None
        assert config.auto_reconnect is True
        assert config.message_history_limit == 100
        assert config.registry_url == "http://localhost:8080"
        assert config.webhook_host == "0.0.0.0"
        assert config.webhook_port == 5000
        assert config.webhook_url is None
        assert config.category == "general"
        assert config.tags is None
        assert config.summary is None
        assert config.developer_keypair_path is None
        assert config.entity_index is None
        assert config.mqtt_broker_url is None
        assert config.default_outbox_topic is None
        assert config.price is None
        assert config.config_dir is None

    def test_custom_values(self):
        config = AgentConfig(
            name="TestAgent",
            description="A test agent",
            capabilities={"ai": ["nlp"]},
            webhook_host="127.0.0.1",
            webhook_port=8080,
            category="finance",
            tags=["stocks", "analysis"],
            summary="A stock analysis agent",
            price="$0.05",
            config_dir=".agent-custom",
        )
        assert config.name == "TestAgent"
        assert config.description == "A test agent"
        assert config.capabilities == {"ai": ["nlp"]}
        assert config.webhook_host == "127.0.0.1"
        assert config.webhook_port == 8080
        assert config.category == "finance"
        assert config.tags == ["stocks", "analysis"]
        assert config.summary == "A stock analysis agent"
        assert config.price == "$0.05"
        assert config.config_dir == ".agent-custom"


class TestAgentConfigCommunicationMode:
    """Test that webhook vs MQTT is determined by config values."""

    def test_webhook_mode_default(self):
        """Default config should use webhook mode (webhook_port is set, mqtt is None)."""
        config = AgentConfig()
        assert config.webhook_port is not None
        assert config.mqtt_broker_url is None

    def test_mqtt_mode_config(self):
        config = AgentConfig(
            mqtt_broker_url="mqtt://broker:1883",
            webhook_port=None,
        )
        assert config.mqtt_broker_url == "mqtt://broker:1883"
        assert config.webhook_port is None


class TestAgentConfigSerialization:
    def test_model_dump(self):
        config = AgentConfig(name="Test")
        d = config.model_dump()
        assert isinstance(d, dict)
        assert d["name"] == "Test"
        assert "webhook_port" in d
        assert "category" in d

    def test_model_from_dict(self):
        config = AgentConfig(**{"name": "FromDict", "webhook_port": 9000, "category": "ai"})
        assert config.name == "FromDict"
        assert config.webhook_port == 9000
        assert config.category == "ai"
