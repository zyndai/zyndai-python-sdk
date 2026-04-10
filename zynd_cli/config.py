"""
CLI config management for ~/.zynd/ directory.

Layout:
  ~/.zynd/
    config.json        — default registry URL, preferences
    developer.json     — developer Ed25519 keypair
    agents/
      <agent-name>/    — per-agent directory
        keypair.json   — agent Ed25519 keypair
"""

import json
import os
from pathlib import Path

DEFAULT_REGISTRY_URL = "https://dns01.zynd.ai"
ZYND_DIR_NAME = ".zynd"
AGENTS_DIR_NAME = "agents"
SERVICES_DIR_NAME = "services"
CONFIG_FILE = "config.json"
DEVELOPER_KEY_FILE = "developer.json"


def zynd_dir() -> Path:
    """Return ~/.zynd/, respecting ZYND_HOME env var."""
    return Path(os.environ.get("ZYND_HOME", Path.home() / ZYND_DIR_NAME))


def ensure_zynd_dir() -> Path:
    """Create ~/.zynd/, ~/.zynd/agents/, and ~/.zynd/services/ if they don't exist."""
    d = zynd_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / AGENTS_DIR_NAME).mkdir(exist_ok=True)
    (d / SERVICES_DIR_NAME).mkdir(exist_ok=True)
    return d


def config_path() -> Path:
    return zynd_dir() / CONFIG_FILE


def developer_key_path() -> Path:
    return zynd_dir() / DEVELOPER_KEY_FILE


def agents_dir() -> Path:
    return zynd_dir() / AGENTS_DIR_NAME


def agent_dir(agent_name: str) -> Path:
    """Return ~/.zynd/agents/<agent_name>/."""
    safe_name = agent_name.lower().replace(" ", "-")
    return agents_dir() / safe_name


def agent_keypair_path(agent_name: str) -> Path:
    """Return ~/.zynd/agents/<agent_name>/keypair.json."""
    return agent_dir(agent_name) / "keypair.json"


def services_dir() -> Path:
    return zynd_dir() / SERVICES_DIR_NAME


def service_dir(service_name: str) -> Path:
    """Return ~/.zynd/services/<service_name>/."""
    safe_name = service_name.lower().replace(" ", "-")
    return services_dir() / safe_name


def service_keypair_path(service_name: str) -> Path:
    """Return ~/.zynd/services/<service_name>/keypair.json."""
    return service_dir(service_name) / "keypair.json"


def load_config() -> dict:
    """Load ~/.zynd/config.json, returning defaults if missing."""
    p = config_path()
    if p.exists():
        with open(p) as f:
            return json.load(f)
    return {}


def save_config(cfg: dict) -> None:
    ensure_zynd_dir()
    with open(config_path(), "w") as f:
        json.dump(cfg, f, indent=2)


def get_registry_url(cli_flag: str | None = None) -> str:
    """Resolve registry URL: CLI flag > env var > config file > default."""
    if cli_flag:
        return cli_flag.rstrip("/")
    env = os.environ.get("ZYND_REGISTRY_URL")
    if env:
        return env.rstrip("/")
    cfg = load_config()
    url = cfg.get("registry_url", DEFAULT_REGISTRY_URL)
    return url.rstrip("/")
