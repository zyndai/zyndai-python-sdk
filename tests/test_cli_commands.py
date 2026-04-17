"""Tests for zynd CLI commands."""

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from zynd_cli.config import (
    zynd_dir,
    ensure_zynd_dir,
    config_path,
    developer_key_path,
    agents_dir,
    load_config,
    save_config,
    get_registry_url,
    DEFAULT_REGISTRY_URL,
)


@pytest.fixture
def tmp_zynd_home(tmp_path):
    """Use a temp directory as ZYND_HOME."""
    with mock.patch.dict(os.environ, {"ZYND_HOME": str(tmp_path / ".zynd")}):
        yield tmp_path / ".zynd"


class TestConfig:
    def test_zynd_dir_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZYND_HOME", None)
            d = zynd_dir()
            assert d == Path.home() / ".zynd"

    def test_zynd_dir_custom(self):
        with mock.patch.dict(os.environ, {"ZYND_HOME": "/tmp/custom-zynd"}):
            assert zynd_dir() == Path("/tmp/custom-zynd")

    def test_ensure_zynd_dir(self, tmp_zynd_home):
        d = ensure_zynd_dir()
        assert d.exists()
        assert (d / "agents").exists()

    def test_save_load_config(self, tmp_zynd_home):
        save_config({"registry_url": "https://test.example.com"})
        cfg = load_config()
        assert cfg["registry_url"] == "https://test.example.com"

    def test_get_registry_url_default(self, tmp_zynd_home):
        assert get_registry_url() == DEFAULT_REGISTRY_URL

    def test_get_registry_url_cli_flag(self, tmp_zynd_home):
        assert get_registry_url("https://custom.example.com/") == "https://custom.example.com"

    def test_get_registry_url_env(self, tmp_zynd_home):
        with mock.patch.dict(os.environ, {"ZYND_REGISTRY_URL": "https://env.example.com"}):
            assert get_registry_url() == "https://env.example.com"

    def test_get_registry_url_config_file(self, tmp_zynd_home):
        save_config({"registry_url": "https://file.example.com"})
        assert get_registry_url() == "https://file.example.com"

    def test_get_registry_url_precedence(self, tmp_zynd_home):
        """CLI flag > env > config."""
        save_config({"registry_url": "https://file.example.com"})
        with mock.patch.dict(os.environ, {"ZYND_REGISTRY_URL": "https://env.example.com"}):
            assert get_registry_url("https://cli.example.com") == "https://cli.example.com"


class TestInitCommand:
    def test_init_creates_keypair(self, tmp_zynd_home):
        from zynd_cli.commands.init_cmd import run
        import argparse

        args = argparse.Namespace(force=False)
        run(args)

        key_path = developer_key_path()
        assert key_path.exists()

        with open(key_path) as f:
            data = json.load(f)
        assert "public_key" in data
        assert "private_key" in data

    def test_init_no_overwrite(self, tmp_zynd_home, capsys):
        from zynd_cli.commands.init_cmd import run
        import argparse

        # First init
        run(argparse.Namespace(force=False))
        key_path = developer_key_path()
        with open(key_path) as f:
            first_data = json.load(f)

        # Second init without --force
        run(argparse.Namespace(force=False))
        with open(key_path) as f:
            second_data = json.load(f)

        # Should not overwrite
        assert first_data["public_key"] == second_data["public_key"]

    def test_init_force_overwrite(self, tmp_zynd_home):
        from zynd_cli.commands.init_cmd import run
        import argparse

        run(argparse.Namespace(force=False))
        with open(developer_key_path()) as f:
            first = json.load(f)

        run(argparse.Namespace(force=True))
        with open(developer_key_path()) as f:
            second = json.load(f)

        # Keys should differ (extremely unlikely to collide)
        assert first["public_key"] != second["public_key"]


class TestKeysCommand:
    def test_keys_list_empty(self, tmp_zynd_home, capsys):
        from zynd_cli.commands.keys import run
        import argparse

        ensure_zynd_dir()
        run(argparse.Namespace(keys_action="list"))
        out = capsys.readouterr().out
        assert "No keypairs found" in out

    def test_keys_list_with_developer(self, tmp_zynd_home, capsys):
        from zynd_cli.commands.init_cmd import run as init_run
        from zynd_cli.commands.keys import run as keys_run
        import argparse

        init_run(argparse.Namespace(force=False))
        keys_run(argparse.Namespace(keys_action="list"))
        out = capsys.readouterr().out
        assert "developer" in out
        assert "zns:" in out

    def test_keys_create(self, tmp_zynd_home, capsys):
        from zynd_cli.commands.keys import run
        import argparse

        ensure_zynd_dir()
        run(argparse.Namespace(keys_action="create", name=None))
        out = capsys.readouterr().out
        assert "agent-0" in out
        assert (agents_dir() / "agent-0.json").exists()

    def test_keys_create_named(self, tmp_zynd_home):
        from zynd_cli.commands.keys import run
        import argparse

        ensure_zynd_dir()
        run(argparse.Namespace(keys_action="create", name="my-agent"))
        assert (agents_dir() / "my-agent.json").exists()

    def test_keys_derive(self, tmp_zynd_home, capsys):
        from zynd_cli.commands.init_cmd import run as init_run
        from zynd_cli.commands.keys import run as keys_run
        import argparse

        init_run(argparse.Namespace(force=False))
        keys_run(argparse.Namespace(keys_action="derive", index=0))
        out = capsys.readouterr().out
        assert "agent-0" in out
        assert (agents_dir() / "agent-0.json").exists()

    def test_keys_derive_deterministic(self, tmp_zynd_home):
        """Deriving with same index should produce same keypair."""
        from zynd_cli.commands.init_cmd import run as init_run
        from zynd_cli.commands.keys import run as keys_run
        from zyndai_agent.ed25519_identity import load_keypair
        import argparse

        init_run(argparse.Namespace(force=False))

        keys_run(argparse.Namespace(keys_action="derive", index=5))
        kp1 = load_keypair(str(agents_dir() / "agent-5.json"))

        keys_run(argparse.Namespace(keys_action="derive", index=5))
        kp2 = load_keypair(str(agents_dir() / "agent-5.json"))

        assert kp1.public_key_b64 == kp2.public_key_b64

    def test_keys_show(self, tmp_zynd_home, capsys):
        from zynd_cli.commands.init_cmd import run as init_run
        from zynd_cli.commands.keys import run as keys_run
        import argparse

        init_run(argparse.Namespace(force=False))
        keys_run(argparse.Namespace(keys_action="show", name="developer"))
        out = capsys.readouterr().out
        assert "developer" in out
        assert "Public key:" in out


class TestSearchCommand:
    @mock.patch("zyndai_agent.dns_registry.search_entities")
    def test_search_json(self, mock_search, tmp_zynd_home, capsys):
        mock_search.return_value = {
            "results": [{"entity_id": "agdns:abc123", "name": "TestBot", "category": "test"}],
            "total_found": 1,
            "has_more": False,
        }
        from zynd_cli.commands.search import run
        import argparse

        args = argparse.Namespace(
            query="test",
            category=None,
            tags=None,
            max_results=10,
            federated=False,
            output_json=True,
            registry=None,
        )
        run(args)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["total_found"] == 1
        assert data["results"][0]["name"] == "TestBot"

    @mock.patch("zyndai_agent.dns_registry.search_entities")
    def test_search_pretty(self, mock_search, tmp_zynd_home, capsys):
        mock_search.return_value = {
            "results": [
                {"entity_id": "agdns:abc123", "name": "TestBot", "category": "test", "entity_url": "http://localhost:5000"}
            ],
            "total_found": 1,
            "has_more": False,
        }
        from zynd_cli.commands.search import run
        import argparse

        args = argparse.Namespace(
            query="test",
            category=None,
            tags=None,
            max_results=10,
            federated=False,
            output_json=False,
            registry=None,
        )
        run(args)
        out = capsys.readouterr().out
        assert "TestBot" in out
        assert "agdns:abc123" in out


class TestResolveCommand:
    @mock.patch("zynd_cli.commands.resolve.get_entity")
    def test_resolve_found(self, mock_get, tmp_zynd_home, capsys):
        mock_get.return_value = {
            "entity_id": "agdns:abc123",
            "name": "TestBot",
            "entity_url": "http://localhost:5000",
            "category": "test",
            "public_key": "ed25519:AAAA",
        }
        from zynd_cli.commands.resolve import run
        import argparse

        args = argparse.Namespace(entity_id="agdns:abc123", output_json=False, registry=None)
        run(args)
        out = capsys.readouterr().out
        assert "TestBot" in out

    @mock.patch("zynd_cli.commands.resolve.get_entity")
    def test_resolve_not_found(self, mock_get, tmp_zynd_home):
        mock_get.return_value = None
        from zynd_cli.commands.resolve import run
        import argparse

        args = argparse.Namespace(entity_id="agdns:missing", output_json=False, registry=None)
        with pytest.raises(SystemExit):
            run(args)


class TestStatusCommand:
    @mock.patch("requests.get")
    def test_status_ok(self, mock_get, tmp_zynd_home, capsys):
        mock_resp = mock.Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": "healthy",
            "node_id": "node-1",
            "version": "1.0.0",
            "agent_count": 42,
            "peer_count": 3,
            "uptime": "24h",
        }
        mock_get.return_value = mock_resp

        from zynd_cli.commands.status import run
        import argparse

        args = argparse.Namespace(output_json=False, registry=None)
        run(args)
        out = capsys.readouterr().out
        assert "healthy" in out
        assert "42" in out


class TestCardCommand:
    def test_card_init_derives_keypair_and_writes_dotenv(self, tmp_zynd_home, tmp_path, capsys, monkeypatch):
        """card init should derive a keypair and add its path to .env."""
        from zynd_cli.commands.init_cmd import run as init_run
        from zynd_cli.commands.card import run as card_run
        import argparse

        # Create developer keypair first
        init_run(argparse.Namespace(force=False))

        # cd into tmp_path so .env is created there
        monkeypatch.chdir(tmp_path)

        args = argparse.Namespace(
            card_action="init",
            index=0,
            registry=None,
        )
        card_run(args)

        # No card file created — that happens at runtime
        assert not (tmp_path / ".well-known" / "agent.json").exists()

        # Keypair should be created
        kp_path = agents_dir() / "agent-0.json"
        assert kp_path.exists()

        # .env should contain the keypair path
        dotenv_path = tmp_path / ".env"
        assert dotenv_path.exists()
        env_content = dotenv_path.read_text()
        assert "ZYND_AGENT_KEYPAIR_PATH=" in env_content
        assert "agent-0.json" in env_content

    def test_card_init_reuses_existing_keypair(self, tmp_zynd_home, tmp_path, capsys, monkeypatch):
        """card init should reuse an existing keypair at the given index."""
        from zynd_cli.commands.init_cmd import run as init_run
        from zynd_cli.commands.card import run as card_run
        import argparse

        init_run(argparse.Namespace(force=False))
        monkeypatch.chdir(tmp_path)

        # Run twice with same index
        args = argparse.Namespace(card_action="init", index=0, registry=None)
        card_run(args)

        # Remove .env so second run can write it again
        (tmp_path / ".env").unlink()

        args2 = argparse.Namespace(card_action="init", index=0, registry=None)
        card_run(args2)

        out = capsys.readouterr().out
        assert "Using existing keypair" in out

    def test_card_show_local_file(self, tmp_zynd_home, tmp_path, capsys):
        from zynd_cli.commands.card import run
        import argparse

        card_path = str(tmp_path / "agent.json")
        card_data = {"name": "My Agent", "description": "Test", "version": "1.0"}
        with open(card_path, "w") as f:
            json.dump(card_data, f)

        args = argparse.Namespace(
            card_action="show",
            entity_id=None,
            file=card_path,
            output_json=True,
            registry=None,
        )
        run(args)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["name"] == "My Agent"


class TestKeysDerivationMetadata:
    def test_derive_saves_metadata(self, tmp_zynd_home):
        from zynd_cli.commands.init_cmd import run as init_run
        from zynd_cli.commands.keys import run as keys_run
        from zyndai_agent.ed25519_identity import load_keypair_with_metadata
        import argparse

        init_run(argparse.Namespace(force=False))
        keys_run(argparse.Namespace(keys_action="derive", index=0))

        kp_path = agents_dir() / "agent-0.json"
        kp, meta = load_keypair_with_metadata(str(kp_path))
        assert meta is not None
        assert "developer_public_key" in meta
        assert meta["index"] == 0


class TestCLIEntryPoint:
    def test_version(self, capsys):
        from zynd_cli.main import main
        import sys

        with mock.patch.object(sys, "argv", ["zynd", "--version"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "zynd" in out

    def test_no_command_shows_help(self, capsys):
        from zynd_cli.main import main
        import sys

        with mock.patch.object(sys, "argv", ["zynd"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
