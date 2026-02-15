import json
import requests
from zyndai_agent.search import SearchAndDiscoveryManager
from zyndai_agent.identity import IdentityManager
from zyndai_agent.communication import AgentCommunicationManager
from zyndai_agent.webhook_communication import WebhookCommunicationManager
from zyndai_agent.payment import X402PaymentProcessor
from zyndai_agent.config_manager import ConfigManager
from pydantic import BaseModel
from typing import Optional

try:
    from langgraph.graph.state import CompiledStateGraph
except ImportError:
    CompiledStateGraph = None

class AgentConfig(BaseModel):
    name: str = ""
    description: str = ""
    capabilities: Optional[dict] = None

    auto_reconnect: bool = True
    message_history_limit: int = 100
    registry_url: str = "http://localhost:3002"

    # Webhook configuration (new)
    webhook_host: Optional[str] = "0.0.0.0"
    webhook_port: Optional[int] = 5000
    webhook_url: Optional[str] = None  # Public URL if behind NAT
    api_key: Optional[str] = None  # API key for webhook registration

    # MQTT configuration (deprecated, kept for backward compatibility)
    mqtt_broker_url: Optional[str] = None
    default_outbox_topic: Optional[str] = None

    price: Optional[str] = None

    # Config directory for agent identity (allows multiple agents in same project)
    config_dir: Optional[str] = None  # e.g., ".agent-stock" or ".agent-user"

class ZyndAIAgent(SearchAndDiscoveryManager, IdentityManager, X402PaymentProcessor, WebhookCommunicationManager):

    def __init__(self, agent_config: AgentConfig):

        self.agent_executor: CompiledStateGraph = None
        self.agent_config = agent_config
        self.communication_mode = None  # Track which mode is active

        # Load or create agent config from .agent/config.json
        config = ConfigManager.load_or_create(agent_config)
        self.registry_agent_id = config["id"]
        self.agent_id = config["id"]
        self.secret_seed = config["seed"]
        self.identity_credential = config["did"]

        self.x402_processor = X402PaymentProcessor(self.secret_seed)
        self.pay_to_address = self.x402_processor.account.address

        IdentityManager.__init__(self, agent_config.registry_url)

        SearchAndDiscoveryManager.__init__(
            self,
            registry_url=agent_config.registry_url
        )

        # Determine communication mode: webhook or MQTT
        # Prefer webhook if webhook_port is configured
        if agent_config.webhook_port is not None and agent_config.mqtt_broker_url is None:
            # Use webhook mode
            self.communication_mode = "webhook"
            WebhookCommunicationManager.__init__(
                self,
                agent_id=self.identity_credential["issuer"],
                webhook_host=agent_config.webhook_host,
                webhook_port=agent_config.webhook_port,
                webhook_url=agent_config.webhook_url,
                auto_restart=agent_config.auto_reconnect,
                message_history_limit=agent_config.message_history_limit,
                identity_credential=self.identity_credential,
                price=agent_config.price,
                pay_to_address=self.pay_to_address
            )
            self.update_agent_webhook_info()

        elif agent_config.mqtt_broker_url is not None:
            # Use MQTT mode (backward compatibility)
            self.communication_mode = "mqtt"
            AgentCommunicationManager.__init__(
                self,
                self.identity_credential["issuer"],
                default_inbox_topic=f"{self.identity_credential['issuer']}/inbox",
                default_outbox_topic=agent_config.default_outbox_topic,
                auto_reconnect=True,
                message_history_limit=agent_config.message_history_limit,
                identity_credential=self.identity_credential,
                secret_seed=self.secret_seed,
                mqtt_broker_url=agent_config.mqtt_broker_url
            )
            self.update_agent_mqtt_info()
        else:
            raise ValueError("Either webhook_port or mqtt_broker_url must be configured")

        # Display agent info
        self._display_agent_info()



    def set_agent_executor(self, agent_executor: CompiledStateGraph):
        """Set the agent executor for the agent."""
        self.agent_executor = agent_executor 

    def update_agent_mqtt_info(self):
        """Updates the mqtt connection info of the agent into the registry so other agents can find me"""

        updateResponse = requests.patch(
            f"{self.agent_config.registry_url}/agents/update-mqtt",
            data={
                "seed": self.secret_seed,
                "mqttUri": self.agent_config.mqtt_broker_url
            }
        )

        if (updateResponse.status_code != 201):
            raise Exception("Failed to update agent connection info in p3 registry.")

        print("Synced with the registry...")

    def update_agent_webhook_info(self):
        """Updates the webhook URL of the agent into the registry so other agents can find me"""
        if not self.agent_config.api_key:
            raise ValueError("API key is required for webhook registration. Please provide api_key in AgentConfig.")

        headers = {
            "accept": "*/*",
            "X-API-KEY": self.agent_config.api_key
        }

        payload = {
            "agentId": self.registry_agent_id,
            "httpWebhookUrl": self.webhook_url
        }

        print(f"Updating webhook URL: {payload}")

        updateResponse = requests.patch(
            f"{self.agent_config.registry_url}/agents/update-webhook",
            json=payload,
            headers=headers
        )

        if updateResponse.status_code != 200:
            raise Exception(f"Failed to update agent webhook info in Zynd registry. Status: {updateResponse.status_code}, Response: {updateResponse.text}")

        print("Synced webhook URL with the registry...")

    def _display_agent_info(self):
        """Display agent information in a pretty format on startup."""
        name = self.agent_config.name or "Unnamed Agent"
        description = self.agent_config.description or "-"
        agent_id = self.agent_id
        address = self.pay_to_address
        did = self.identity_credential.get("issuer", "-")
        mode = self.communication_mode or "-"
        webhook_url = getattr(self, "webhook_url", None)
        price = self.agent_config.price or "Free"

        border = "=" * 60
        print(f"\n{border}")
        print(f"  ZYND AI AGENT")
        print(f"{border}")
        print(f"  Name        : {name}")
        print(f"  Description : {description}")
        print(f"  Agent ID    : {agent_id}")
        print(f"  DID         : {did}")
        print(f"  Address     : {address}")
        print(f"  Mode        : {mode}")
        if webhook_url:
            print(f"  Webhook URL : {webhook_url}")
        print(f"  Price       : {price}")
        print(f"{border}\n")

    def update_agent_connection_info(self):
        """Updates the agent connection info (webhook or MQTT) in the registry based on communication mode"""
        if self.communication_mode == "webhook":
            self.update_agent_webhook_info()
        elif self.communication_mode == "mqtt":
            self.update_agent_mqtt_info()
        else:
            raise ValueError(f"Unknown communication mode: {self.communication_mode}")