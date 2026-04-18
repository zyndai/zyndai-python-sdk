import os
import json
import time
import logging

from zyndai_agent.ed25519_identity import (
    Ed25519Keypair,
    generate_keypair,
    keypair_from_private_bytes,
    derive_agent_keypair,
    load_keypair,
)
from zyndai_agent import dns_registry

logger = logging.getLogger(__name__)


class ConfigManager:
    """
    Manages agent configuration stored in .agent/config.json.

    v2 format uses Ed25519 identity with agent-dns decentralized registry.
    Supports auto-migration from v1 (PolygonID/DID) configs.
    """

    DEFAULT_CONFIG_DIR = ".agent"
    CONFIG_FILE = "config.json"

    @staticmethod
    def _config_path(config_dir: str = None):
        dir_name = config_dir or ConfigManager.DEFAULT_CONFIG_DIR
        return os.path.join(os.getcwd(), dir_name, ConfigManager.CONFIG_FILE)

    @staticmethod
    def _config_dir(config_dir: str = None):
        dir_name = config_dir or ConfigManager.DEFAULT_CONFIG_DIR
        return os.path.join(os.getcwd(), dir_name)

    @staticmethod
    def load_config(config_dir: str = None):
        """Load existing config from .agent/config.json. Returns None if not found."""
        config_path = ConfigManager._config_path(config_dir)
        if not os.path.exists(config_path):
            return None

        try:
            with open(config_path, "r") as f:
                config = json.load(f)
        except json.JSONDecodeError:
            print(f"Warning: {config_path} is corrupted. Creating a new agent...")
            return None

        print(f"Loaded agent config from {config_path}")
        return config

    @staticmethod
    def save_config(config: dict, config_dir: str = None):
        """Save config to .agent/config.json, creating the directory if needed."""
        dir_path = ConfigManager._config_dir(config_dir)
        os.makedirs(dir_path, exist_ok=True)

        config_path = ConfigManager._config_path(config_dir)
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)

        print(f"Saved agent config to {config_path}")

    @staticmethod
    def _is_legacy_config(config: dict) -> bool:
        """Detect legacy v1 config by presence of 'didIdentifier' key."""
        return "didIdentifier" in config and "schema_version" not in config

    @staticmethod
    def _migrate_legacy_config(config: dict, agent_config) -> dict:
        """
        Migrate a v1 config (PolygonID/DID) to v2 (Ed25519/agent-dns).

        Preserves old seed as 'legacy_seed' for x402 payment continuity.
        """
        print("Detected legacy v1 config. Migrating to v2 (agent-dns)...")

        # Generate new Ed25519 keypair
        kp = generate_keypair()

        # Derive new agent ID
        entity_id = kp.entity_id

        # Build agent URL from webhook config
        entity_url = _build_entity_url(agent_config)

        # Register on new registry
        try:
            dns_registry.register_entity(
                registry_url=agent_config.registry_url,
                keypair=kp,
                name=config.get("name", agent_config.name),
                entity_url=entity_url,
                category=getattr(agent_config, "category", "general"),
                tags=getattr(agent_config, "tags", None),
                summary=getattr(agent_config, "summary", None),
            )
            print(f"Registered migrated agent on agent-dns: {entity_id}")
        except Exception as e:
            print(f"Warning: Could not register on agent-dns during migration: {e}")

        new_config = {
            "schema_version": "2.0",
            "entity_id": entity_id,
            "public_key": kp.public_key_string,
            "private_key": kp.private_key_b64,
            "name": config.get("name", agent_config.name),
            "description": config.get("description", agent_config.description),
            "entity_url": entity_url,
            "registry_url": agent_config.registry_url,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "legacy_seed": config.get("seed"),  # Preserve for x402 payment continuity
        }

        return new_config

    @staticmethod
    def create_agent(agent_config, config_dir: str = None) -> dict:
        """
        Create a new agent with Ed25519 identity and register on agent-dns.

        Args:
            agent_config: AgentConfig instance
            config_dir: Custom config directory

        Returns:
            dict: The saved config in v2 format
        """
        # Generate or derive keypair
        dev_kp_path = getattr(agent_config, "developer_keypair_path", None)
        entity_index = getattr(agent_config, "entity_index", None)

        if dev_kp_path and entity_index is not None:
            # HD derivation from developer key
            dev_kp = load_keypair(dev_kp_path)
            kp = derive_agent_keypair(dev_kp.private_key, entity_index)
        else:
            # Generate fresh keypair locally
            kp = generate_keypair()

        entity_id = kp.entity_id

        # Build agent URL
        entity_url = _build_entity_url(agent_config)

        # Register on agent-dns mesh
        try:
            dns_registry.register_entity(
                registry_url=agent_config.registry_url,
                keypair=kp,
                name=agent_config.name,
                entity_url=entity_url,
                category=getattr(agent_config, "category", "general"),
                tags=getattr(agent_config, "tags", None),
                summary=getattr(agent_config, "summary", None),
            )
        except Exception as e:
            print(f"Warning: Could not register on agent-dns: {e}")
            print("Agent will operate with local identity only.")

        config = {
            "schema_version": "2.0",
            "entity_id": entity_id,
            "public_key": kp.public_key_string,
            "private_key": kp.private_key_b64,
            "name": agent_config.name,
            "description": agent_config.description,
            "entity_url": entity_url,
            "registry_url": agent_config.registry_url,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        ConfigManager.save_config(config, config_dir)
        return config

    @staticmethod
    def load_or_create(agent_config):
        """
        Load existing agent config or create a new agent.

        If .agent/config.json exists:
          - If legacy v1 format: auto-migrate to v2
          - If v2 format: return as-is
        Otherwise, create new agent with Ed25519 identity.

        Args:
            agent_config: AgentConfig instance

        Returns:
            dict with keys: entity_id, public_key, private_key, name, description, etc.
        """
        config_dir = getattr(agent_config, "config_dir", None)
        config = ConfigManager.load_config(config_dir)

        if config is not None:
            # Check if this is a legacy config that needs migration
            if ConfigManager._is_legacy_config(config):
                config = ConfigManager._migrate_legacy_config(config, agent_config)
                ConfigManager.save_config(config, config_dir)
                return config
            return config

        # Validate required fields for agent creation
        if not agent_config.name:
            raise ValueError("name is required in AgentConfig to create a new agent.")
        if not agent_config.capabilities:
            raise ValueError("capabilities is required in AgentConfig to create a new agent.")

        dir_name = config_dir or ConfigManager.DEFAULT_CONFIG_DIR
        print(f"No {dir_name}/config.json found. Creating a new agent...")
        return ConfigManager.create_agent(
            agent_config=agent_config,
            config_dir=config_dir,
        )


def _build_entity_url(agent_config) -> str:
    """Build the public URL advertised to the registry.

    Precedence:
      1. ``entity_url`` — explicit public URL (preferred). Used by hosting
         layers such as zynd-deployer to advertise an HTTPS endpoint while
         the webhook server still binds to 0.0.0.0:5000 inside the process.
      2. ``webhook_url`` — deprecated alias for ``entity_url``. A warning is
         logged when this is used without ``entity_url`` also being set.
      3. Fallback: derived from ``webhook_host`` / ``webhook_port``. When
         ``webhook_host == "0.0.0.0"`` we map that to ``localhost`` so the
         URL is dereferenceable; scheme is ``https`` only for port 443.
    """
    entity_url = getattr(agent_config, "entity_url", None)
    if entity_url:
        url = entity_url
        if url.endswith("/webhook"):
            return url[: -len("/webhook")]
        return url.rstrip("/")

    webhook_url = getattr(agent_config, "webhook_url", None)
    if webhook_url:
        logger.warning(
            "ZyndBaseConfig.webhook_url is deprecated; rename it to "
            "entity_url. Support for webhook_url will be removed in a "
            "future release."
        )
        if webhook_url.endswith("/webhook"):
            return webhook_url[: -len("/webhook")]
        return webhook_url.rstrip("/")

    host = getattr(agent_config, "webhook_host", "0.0.0.0")
    port = getattr(agent_config, "webhook_port", 5000)
    if host == "0.0.0.0":
        host = "localhost"
    scheme = "https" if port == 443 else "http"
    return f"{scheme}://{host}:{port}"
