import json
import requests
from zyndai_agent.search import SearchAndDiscoveryManager
from zyndai_agent.identity import IdentityManager
from zyndai_agent.communication import AgentCommunicationManager
from zyndai_agent.payment import X402PaymentProcessor
from pydantic import BaseModel
from typing import Optional
from langchain.agents import create_agent
from langgraph.graph.state import CompiledStateGraph

class AgentConfig(BaseModel):
    auto_reconnect: bool = True
    message_history_limit: int = 100
    registry_url: str = "http://localhost:3002"
    mqtt_broker_url: str
    identity_credential_path: str
    identity_credential: Optional[dict] = None
    default_outbox_topic: Optional[str] = None
    secret_seed: str = None

class ZyndAIAgent(SearchAndDiscoveryManager, IdentityManager, AgentCommunicationManager, X402PaymentProcessor):

    def __init__(self, agent_config: AgentConfig): 

        self.agent_executor: CompiledStateGraph = None
        self.agent_config = agent_config 
        self.x402_processor = X402PaymentProcessor(agent_config.secret_seed)

        try:
            with open(agent_config.identity_credential_path, "r") as f:
                self.identity_credential = json.load(f)
        except FileNotFoundError:
            raise FileNotFoundError(f"Identity credential file not found at {agent_config.identity_credential_path}")
        
        IdentityManager.__init__(self,agent_config.registry_url)

        SearchAndDiscoveryManager.__init__(
            self,
            registry_url=agent_config.registry_url
        )
        
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