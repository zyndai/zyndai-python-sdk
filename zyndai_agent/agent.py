import json
import requests
from zyndai_agent.search import SearchAndDiscoveryManager
from zyndai_agent.identity import IdentityManager
from zyndai_agent.communication import AgentCommunicationManager
from zyndai_agent.webhook_communication import WebhookCommunicationManager
from zyndai_agent.payment import X402PaymentProcessor
from pydantic import BaseModel
from typing import Optional
from langchain.agents import create_agent
from langgraph.graph.state import CompiledStateGraph

class AgentConfig(BaseModel):
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

    # Common configuration
    identity_credential_path: str
    identity_credential: Optional[dict] = None
    secret_seed: str = None

class ZyndAIAgent(SearchAndDiscoveryManager, IdentityManager, X402PaymentProcessor):

    def __init__(self, agent_config: AgentConfig):

        self.agent_executor: CompiledStateGraph = None
        self.agent_config = agent_config
        self.x402_processor = X402PaymentProcessor(agent_config.secret_seed)
        self.communication_mode = None  # Track which mode is active

        try:
            with open(agent_config.identity_credential_path, "r") as f:
                self.identity_credential = json.load(f)
        except FileNotFoundError:
            raise FileNotFoundError(f"Identity credential file not found at {agent_config.identity_credential_path}")

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
                secret_seed=agent_config.secret_seed
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
                secret_seed=agent_config.secret_seed,
                mqtt_broker_url=agent_config.mqtt_broker_url
            )
            self.update_agent_mqtt_info()
        else:
            raise ValueError("Either webhook_port or mqtt_broker_url must be configured")



    def set_agent_executor(self, agent_executor: CompiledStateGraph):
        """Set the agent executor for the agent."""
        self.agent_executor = agent_executor 

    def update_agent_mqtt_info(self):
        """Updates the mqtt connection info of the agent into the registry so other agents can find me"""
        print(self.agent_config.secret_seed, self.agent_config.mqtt_broker_url, f"{self.agent_config.registry_url}/agents/update-mqtt")
        updateResponse = requests.post(
            f"{self.agent_config.registry_url}/agents/update-mqtt",
            data={
                "seed": self.agent_config.secret_seed,
                "mqttUri": self.agent_config.mqtt_broker_url
            }
        )
        print(updateResponse.status_code,"====")
        if (updateResponse.status_code != 201):
            raise Exception("Failed to update agent connection info in p3 registry.")

        print("Synced with the registry...")

    def update_agent_webhook_info(self):
        """Updates the webhook URL of the agent into the registry so other agents can find me"""
        if not self.agent_config.api_key:
            raise ValueError("API key is required for webhook registration. Please provide api_key in AgentConfig.")

        print(self.webhook_url, f"{self.agent_config.registry_url}/agents/update-webhook")

        # Prepare headers with API key
        headers = {
            "api-key": self.agent_config.api_key,
            "Content-Type": "application/json"
        }

        # Prepare request body
        payload = {
            "agentId": self.identity_credential["issuer"],
            "httpWebhookUrl": self.webhook_url
        }

        updateResponse = requests.post(
            f"{self.agent_config.registry_url}/agents/update-webhook",
            json=payload,
            headers=headers
        )
        print(updateResponse.status_code, "====")
        if updateResponse.status_code != 200:
            raise Exception(f"Failed to update agent webhook info in p3 registry. Status: {updateResponse.status_code}, Response: {updateResponse.text}")

        print("Synced webhook URL with the registry...")

    def update_agent_connection_info(self):
        """Updates the agent connection info (webhook or MQTT) in the registry based on communication mode"""
        if self.communication_mode == "webhook":
            self.update_agent_webhook_info()
        elif self.communication_mode == "mqtt":
            self.update_agent_mqtt_info()
        else:
            raise ValueError(f"Unknown communication mode: {self.communication_mode}")