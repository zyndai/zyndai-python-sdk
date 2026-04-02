"""
Tests for Agent Card Loader module.
"""

import json
import os
import tempfile

import pytest
from zyndai_agent.ed25519_identity import (
    generate_keypair,
    save_keypair,
    load_keypair_with_metadata,
)
from zyndai_agent.agent_card_loader import (
    load_agent_card,
    resolve_keypair,
    build_runtime_card,
    compute_card_hash,
    resolve_card_from_config,
    load_derivation_metadata,
)


SAMPLE_CARD = {
    "name": "Test Agent",
    "description": "A test agent for unit tests",
    "version": "1.0",
    "category": "test",
    "tags": ["test", "unit"],
    "summary": "Test agent summary",
    "capabilities": [
        {"name": "nlp", "category": "ai"},
        {"name": "http", "category": "protocols"},
    ],
    "pricing": {
        "model": "per-request",
        "currency": "USDC",
        "rates": {"default": 0.0001},
        "payment_methods": ["x402"],
    },
    "server": {
        "host": "0.0.0.0",
        "port": 5003,
        "public_url": None,
        "use_ngrok": False,
    },
    "registry": {
        "url": "https://dns01.zynd.ai",
    },
}


class TestLoadAgentCard:
    def test_load_valid_card(self, tmp_path):
        card_path = tmp_path / "agent.json"
        card_path.write_text(json.dumps(SAMPLE_CARD))

        card = load_agent_card(str(card_path))
        assert card["name"] == "Test Agent"
        assert card["category"] == "test"
        assert len(card["capabilities"]) == 2

    def test_load_missing_file(self):
        with pytest.raises(FileNotFoundError):
            load_agent_card("/nonexistent/path/agent.json")

    def test_load_card_missing_name(self, tmp_path):
        card_path = tmp_path / "agent.json"
        card_path.write_text(json.dumps({"description": "no name"}))

        with pytest.raises(ValueError, match="must have a 'name' field"):
            load_agent_card(str(card_path))

    def test_load_card_invalid_json_type(self, tmp_path):
        card_path = tmp_path / "agent.json"
        card_path.write_text(json.dumps([1, 2, 3]))

        with pytest.raises(ValueError, match="must be a JSON object"):
            load_agent_card(str(card_path))


class TestResolveKeypair:
    def test_resolve_from_env_path(self, tmp_path, monkeypatch):
        kp = generate_keypair()
        kp_path = tmp_path / "agent-0.json"
        save_keypair(kp, str(kp_path))
        monkeypatch.setenv("ZYND_AGENT_KEYPAIR_PATH", str(kp_path))

        # Clear other env vars
        monkeypatch.delenv("ZYND_AGENT_PRIVATE_KEY", raising=False)

        class FakeConfig:
            keypair_path = None
            config_dir = None

        resolved = resolve_keypair(FakeConfig())
        assert resolved.public_key_b64 == kp.public_key_b64

    def test_resolve_from_env_private_key(self, monkeypatch):
        kp = generate_keypair()
        monkeypatch.delenv("ZYND_AGENT_KEYPAIR_PATH", raising=False)
        monkeypatch.setenv("ZYND_AGENT_PRIVATE_KEY", kp.private_key_b64)

        class FakeConfig:
            keypair_path = None
            config_dir = None

        resolved = resolve_keypair(FakeConfig())
        assert resolved.public_key_b64 == kp.public_key_b64

    def test_resolve_from_config_keypair_path(self, tmp_path, monkeypatch):
        kp = generate_keypair()
        kp_path = tmp_path / "agent-0.json"
        save_keypair(kp, str(kp_path))
        monkeypatch.delenv("ZYND_AGENT_KEYPAIR_PATH", raising=False)
        monkeypatch.delenv("ZYND_AGENT_PRIVATE_KEY", raising=False)

        class FakeConfig:
            keypair_path = str(kp_path)
            config_dir = None

        resolved = resolve_keypair(FakeConfig())
        assert resolved.public_key_b64 == kp.public_key_b64

    def test_resolve_raises_when_nothing_found(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ZYND_AGENT_KEYPAIR_PATH", raising=False)
        monkeypatch.delenv("ZYND_AGENT_PRIVATE_KEY", raising=False)
        monkeypatch.chdir(tmp_path)

        class FakeConfig:
            keypair_path = None
            config_dir = None

        with pytest.raises(ValueError, match="No keypair found"):
            resolve_keypair(FakeConfig())


class TestBuildRuntimeCard:
    def test_builds_complete_card(self):
        kp = generate_keypair()
        runtime = build_runtime_card(SAMPLE_CARD, "http://localhost:5003", kp)

        assert runtime["agent_id"] == kp.agent_id
        assert runtime["public_key"] == kp.public_key_string
        assert runtime["name"] == "Test Agent"
        assert runtime["status"] == "online"
        assert "signature" in runtime
        assert runtime["signature"].startswith("ed25519:")

        # Endpoints should be absolute
        assert runtime["endpoints"]["invoke"] == "http://localhost:5003/webhook/sync"
        assert runtime["endpoints"]["health"] == "http://localhost:5003/health"

    def test_strips_server_and_registry(self):
        kp = generate_keypair()
        runtime = build_runtime_card(SAMPLE_CARD, "http://localhost:5003", kp)

        assert "server" not in runtime
        assert "registry" not in runtime

    def test_preserves_pricing(self):
        kp = generate_keypair()
        runtime = build_runtime_card(SAMPLE_CARD, "http://localhost:5003", kp)

        assert runtime["pricing"]["rates"]["default"] == 0.0001


class TestComputeCardHash:
    def test_same_card_same_hash(self):
        h1 = compute_card_hash(SAMPLE_CARD)
        h2 = compute_card_hash(SAMPLE_CARD)
        assert h1 == h2

    def test_different_name_different_hash(self):
        card2 = dict(SAMPLE_CARD, name="Different Name")
        assert compute_card_hash(SAMPLE_CARD) != compute_card_hash(card2)

    def test_ignores_non_metadata_fields(self):
        card_with_extra = dict(SAMPLE_CARD, agent_id="xxx", status="offline")
        assert compute_card_hash(SAMPLE_CARD) == compute_card_hash(card_with_extra)


class TestResolveCardFromConfig:
    def test_converts_legacy_config(self):
        from pydantic import BaseModel
        from typing import Optional, List

        class FakeConfig(BaseModel):
            name: str = "Legacy Agent"
            description: str = "A legacy agent"
            capabilities: Optional[dict] = {"ai": ["nlp"], "protocols": ["http"]}
            category: str = "finance"
            tags: Optional[List[str]] = ["legacy"]
            summary: Optional[str] = "Legacy summary"
            price: Optional[str] = "$0.01"
            webhook_host: str = "0.0.0.0"
            webhook_port: int = 5000
            webhook_url: Optional[str] = None
            use_ngrok: bool = False
            registry_url: str = "https://dns01.zynd.ai"

        config = FakeConfig()
        card = resolve_card_from_config(config)

        assert card["name"] == "Legacy Agent"
        assert card["category"] == "finance"
        assert card["tags"] == ["legacy"]
        assert len(card["capabilities"]) == 2
        assert card["pricing"]["rates"]["default"] == 0.01
        assert card["server"]["port"] == 5000
        assert card["registry"]["url"] == "https://dns01.zynd.ai"


class TestLoadDerivationMetadata:
    def test_loads_metadata(self, tmp_path):
        kp = generate_keypair()
        kp_path = tmp_path / "agent-0.json"
        save_keypair(kp, str(kp_path), derivation_metadata={
            "developer_public_key": "abc123",
            "index": 0,
        })

        meta = load_derivation_metadata(str(kp_path))
        assert meta is not None
        assert meta["developer_public_key"] == "abc123"
        assert meta["index"] == 0

    def test_returns_none_without_metadata(self, tmp_path):
        kp = generate_keypair()
        kp_path = tmp_path / "agent-0.json"
        save_keypair(kp, str(kp_path))

        meta = load_derivation_metadata(str(kp_path))
        assert meta is None

    def test_returns_none_for_missing_file(self):
        meta = load_derivation_metadata("/nonexistent/path.json")
        assert meta is None


class TestSaveKeypairWithMetadata:
    def test_saves_and_loads_metadata(self, tmp_path):
        kp = generate_keypair()
        kp_path = tmp_path / "agent-0.json"
        save_keypair(kp, str(kp_path), derivation_metadata={
            "developer_public_key": "devkey123",
            "index": 42,
        })

        loaded_kp, meta = load_keypair_with_metadata(str(kp_path))
        assert loaded_kp.public_key_b64 == kp.public_key_b64
        assert meta["developer_public_key"] == "devkey123"
        assert meta["index"] == 42

    def test_saves_without_metadata(self, tmp_path):
        kp = generate_keypair()
        kp_path = tmp_path / "agent-0.json"
        save_keypair(kp, str(kp_path))

        loaded_kp, meta = load_keypair_with_metadata(str(kp_path))
        assert loaded_kp.public_key_b64 == kp.public_key_b64
        assert meta is None
