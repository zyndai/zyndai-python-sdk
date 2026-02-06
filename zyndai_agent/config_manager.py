import os
import json
import requests


class ConfigManager:
    """
    Manages agent configuration stored in .agent/config.json.

    On first run, provisions a new agent via the registry API and saves
    the identity credentials locally. On subsequent runs, loads the
    saved config so the user doesn't need to provide identity credentials manually.
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

        with open(config_path, "r") as f:
            config = json.load(f)

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
    def create_agent(registry_url: str, api_key: str, name: str, description: str, capabilities: dict, config_dir: str = None):
        """
        Create a new agent via the registry API.

        Args:
            registry_url: Base URL of the agent registry
            api_key: API key for authentication
            name: Agent display name
            description: Agent description
            capabilities: Agent capabilities dict (e.g. {"ai": ["nlp"], "protocols": ["http"]})
            config_dir: Custom config directory (e.g., ".agent-stock")

        Returns:
            dict: The saved config with id, didIdentifier, did, name, description, seed
        """
        headers = {
            "accept": "application/json",
            "Content-Type": "application/json",
            "x-api-key": api_key
        }

        payload = {
            "name": name,
            "description": description,
            "capabilities": capabilities,
            "status": "ACTIVE"
        }

        response = requests.post(
            f"{registry_url}/agents",
            json=payload,
            headers=headers
        )

        if response.status_code not in (200, 201):
            raise RuntimeError(
                f"Failed to create agent via registry API. "
                f"Status: {response.status_code}, Response: {response.text}"
            )

        data = response.json()

        # The 'did' field in the API response is a JSON string; parse it
        did = data["did"]
        if isinstance(did, str):
            did = json.loads(did)

        config = {
            "id": data["id"],
            "didIdentifier": data["didIdentifier"],
            "did": did,
            "name": data["name"],
            "description": data["description"],
            "seed": data["seed"]
        }

        ConfigManager.save_config(config, config_dir)
        return config

    @staticmethod
    def load_or_create(agent_config):
        """
        Load existing agent config or create a new agent.

        If .agent/config.json exists, returns stored values.
        Otherwise, calls the registry API to provision a new agent.

        Args:
            agent_config: AgentConfig instance with registry_url, api_key, name,
                          description, capabilities, and optional config_dir

        Returns:
            dict with keys: id, didIdentifier, did, name, description, seed
        """
        config_dir = getattr(agent_config, 'config_dir', None)
        config = ConfigManager.load_config(config_dir)
        if config is not None:
            return config

        # Validate required fields for agent creation
        if not agent_config.api_key:
            raise ValueError(
                "api_key is required in AgentConfig to create a new agent. "
                "Provide an API key or place a .agent/config.json in the working directory."
            )
        if not agent_config.name:
            raise ValueError("name is required in AgentConfig to create a new agent.")
        if not agent_config.capabilities:
            raise ValueError("capabilities is required in AgentConfig to create a new agent.")

        dir_name = config_dir or ConfigManager.DEFAULT_CONFIG_DIR
        print(f"No {dir_name}/config.json found. Creating a new agent...")
        return ConfigManager.create_agent(
            registry_url=agent_config.registry_url,
            api_key=agent_config.api_key,
            name=agent_config.name,
            description=agent_config.description,
            capabilities=agent_config.capabilities,
            config_dir=config_dir
        )
