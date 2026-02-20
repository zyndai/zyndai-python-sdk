"""
Tests for ConfigManager: load, save, create, and load_or_create.
"""

import json
import os
import pytest
from unittest.mock import patch, MagicMock
from zyndai_agent.config_manager import ConfigManager
from zyndai_agent.agent import AgentConfig


class TestConfigManagerPaths:
    def test_default_config_dir(self):
        path = ConfigManager._config_path()
        assert path.endswith(os.path.join(".agent", "config.json"))

    def test_custom_config_dir(self):
        path = ConfigManager._config_path(".agent-stock")
        assert path.endswith(os.path.join(".agent-stock", "config.json"))

    def test_config_dir_path(self):
        d = ConfigManager._config_dir(".agent-custom")
        assert d.endswith(".agent-custom")


class TestConfigManagerSaveLoad:
    def test_save_and_load(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = {"id": "test", "name": "TestAgent", "seed": "abc"}
        ConfigManager.save_config(config, ".agent-test")
        loaded = ConfigManager.load_config(".agent-test")
        assert loaded == config

    def test_load_returns_none_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = ConfigManager.load_config(".nonexistent")
        assert result is None

    def test_save_creates_directory(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ConfigManager.save_config({"id": "1"}, ".agent-new")
        assert os.path.exists(tmp_path / ".agent-new" / "config.json")


class TestConfigManagerCreate:
    @patch("zyndai_agent.config_manager.requests.post")
    def test_create_agent_success(self, mock_post, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "id": "new-id",
            "didIdentifier": "did:polygonid:new",
            "did": json.dumps(
                {
                    "issuer": "did:polygonid:new:issuer",
                    "credentialSubject": {
                        "x": "1",
                        "y": "2",
                        "type": "AuthBJJCredential",
                    },
                }
            ),
            "name": "New Agent",
            "description": "New desc",
            "seed": "c2VlZA==",
        }
        mock_post.return_value = mock_response

        result = ConfigManager.create_agent(
            registry_url="http://localhost:3002",
            api_key="key",
            name="New Agent",
            description="New desc",
            capabilities={"ai": ["nlp"]},
            config_dir=".agent-new",
        )

        assert result["id"] == "new-id"
        assert result["name"] == "New Agent"
        assert isinstance(result["did"], dict)  # Should be parsed from JSON string

    @patch("zyndai_agent.config_manager.requests.post")
    def test_create_agent_failure(self, mock_post, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_post.return_value = mock_response

        with pytest.raises(RuntimeError, match="Failed to create agent"):
            ConfigManager.create_agent(
                registry_url="http://localhost:3002",
                api_key="key",
                name="Agent",
                description="desc",
                capabilities={},
            )


class TestLoadOrCreate:
    def test_loads_existing_config(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = {
            "id": "existing",
            "seed": "s",
            "did": {},
            "name": "N",
            "description": "D",
        }
        ConfigManager.save_config(config, ".agent")

        agent_config = AgentConfig(name="N", description="D", api_key="k")
        result = ConfigManager.load_or_create(agent_config)
        assert result["id"] == "existing"

    def test_raises_without_api_key(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        agent_config = AgentConfig(name="N")
        with pytest.raises(ValueError, match="api_key is required"):
            ConfigManager.load_or_create(agent_config)

    def test_raises_without_name(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        agent_config = AgentConfig(api_key="k")
        with pytest.raises(ValueError, match="name is required"):
            ConfigManager.load_or_create(agent_config)

    def test_raises_without_capabilities(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        agent_config = AgentConfig(name="N", api_key="k")
        with pytest.raises(ValueError, match="capabilities is required"):
            ConfigManager.load_or_create(agent_config)
