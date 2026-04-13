"""
Tests for ConfigManager: load, save, create, migration, and load_or_create.
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
        config = {"agent_id": "zns:test", "name": "TestAgent", "schema_version": "2.0"}
        ConfigManager.save_config(config, ".agent-test")
        loaded = ConfigManager.load_config(".agent-test")
        assert loaded == config

    def test_load_returns_none_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = ConfigManager.load_config(".nonexistent")
        assert result is None

    def test_save_creates_directory(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ConfigManager.save_config({"agent_id": "zns:1"}, ".agent-new")
        assert os.path.exists(tmp_path / ".agent-new" / "config.json")


class TestConfigManagerCreate:
    @patch("zyndai_agent.config_manager.dns_registry.register_agent")
    def test_create_agent_success(self, mock_register, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_register.return_value = "zns:test123"

        agent_config = AgentConfig(
            name="New Agent",
            description="New desc",
            capabilities={"ai": ["nlp"]},
            registry_url="http://localhost:8080",
        )

        result = ConfigManager.create_agent(agent_config, ".agent-new")

        assert result["schema_version"] == "2.0"
        assert result["agent_id"].startswith("zns:")
        assert result["public_key"].startswith("ed25519:")
        assert "private_key" in result
        assert result["name"] == "New Agent"
        mock_register.assert_called_once()

    @patch("zyndai_agent.config_manager.dns_registry.register_agent")
    def test_create_agent_registry_failure_still_saves(self, mock_register, tmp_path, monkeypatch):
        """Agent should still be created locally even if registry is down."""
        monkeypatch.chdir(tmp_path)
        mock_register.side_effect = Exception("Connection refused")

        agent_config = AgentConfig(
            name="Offline Agent",
            description="Works offline",
            capabilities={"ai": ["nlp"]},
        )

        result = ConfigManager.create_agent(agent_config, ".agent-offline")
        assert result["agent_id"].startswith("zns:")
        assert os.path.exists(tmp_path / ".agent-offline" / "config.json")


class TestLegacyMigration:
    def test_is_legacy_config(self):
        legacy = {"didIdentifier": "did:polygonid:test", "id": "old-id", "seed": "abc"}
        assert ConfigManager._is_legacy_config(legacy) is True

    def test_v2_config_is_not_legacy(self):
        v2 = {"schema_version": "2.0", "agent_id": "zns:abc"}
        assert ConfigManager._is_legacy_config(v2) is False

    @patch("zyndai_agent.config_manager.dns_registry.register_agent")
    def test_auto_migration(self, mock_register, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_register.return_value = "zns:migrated"

        # Save a legacy v1 config
        legacy_config = {
            "id": "old-uuid",
            "didIdentifier": "did:polygonid:old",
            "did": {"issuer": "did:polygonid:old:issuer"},
            "name": "Old Agent",
            "description": "Old desc",
            "seed": "dGVzdHNlZWQxMjM0NTY3ODkwMTIzNDU2Nzg5MDEyMzQ=",
        }
        ConfigManager.save_config(legacy_config, ".agent")

        agent_config = AgentConfig(
            name="Old Agent",
            description="Old desc",
            capabilities={"ai": ["nlp"]},
        )

        result = ConfigManager.load_or_create(agent_config)

        # Should be migrated to v2
        assert result["schema_version"] == "2.0"
        assert result["agent_id"].startswith("zns:")
        assert result["public_key"].startswith("ed25519:")
        # Legacy seed should be preserved
        assert result["legacy_seed"] == legacy_config["seed"]


class TestLoadOrCreate:
    def test_loads_existing_v2_config(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = {
            "schema_version": "2.0",
            "agent_id": "zns:existing",
            "public_key": "ed25519:AAAA",
            "private_key": "BBBB",
            "name": "N",
            "description": "D",
        }
        ConfigManager.save_config(config, ".agent")

        agent_config = AgentConfig(name="N", description="D")
        result = ConfigManager.load_or_create(agent_config)
        assert result["agent_id"] == "zns:existing"

    def test_raises_without_name(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        agent_config = AgentConfig()
        with pytest.raises(ValueError, match="name is required"):
            ConfigManager.load_or_create(agent_config)

    def test_raises_without_capabilities(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        agent_config = AgentConfig(name="N")
        with pytest.raises(ValueError, match="capabilities is required"):
            ConfigManager.load_or_create(agent_config)
