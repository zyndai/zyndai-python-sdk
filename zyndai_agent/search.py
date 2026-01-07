# Agent Discovery and Search Protocol Module for ZyndAI
import logging
import requests

from typing import List, Optional, TypedDict


logging.basicConfig(
    level=logging.ERROR,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("SearchAndDiscovery")

class AgentSearchResponse(TypedDict):
    id: str
    name: str
    description: str
    mqttUri: Optional[str]  # Deprecated, kept for backward compatibility
    webhookUrl: Optional[str]  # New field for webhook communication
    inboxTopic: Optional[str]
    matchScore: int
    didIdentifier: str
    did: dict

class SearchAndDiscoveryManager:
    """
    This class implements the search and discovery protocol for ZyndAI agents.
    It allows agents to discover each other and share information about their capabilities.
    """

    def __init__(self, registry_url: str = "http://localhost:3002/sdk/search"):

        self.agents = []
        self.registry_url = registry_url


    def search_agents_by_capabilities(self, capabilities: List[str] = [], match_score_gte: float = 0.5, top_k: Optional[int] = None) -> List[AgentSearchResponse]:
        """
        Discover all registered agents in the system based on their capabilities.

        match_score_gte: Minimum match score for agents to be included in the results.
        top_k: Optional parameter to limit the number of results returned or return all.
        """

        logger.info("Discovering agents...")


        resp = requests.post(f"{self.registry_url}/sdk/search", json={"userProvidedCapabilities": capabilities})
        if resp.status_code == 201:
            agents = resp.json()
            logger.info(f"Discovered {len(agents)} agents.")

            filtered_agents = [
                agent for agent in agents
                if agent.get("matchScore", 0) >= match_score_gte
            ]

            if top_k is not None:
                filtered_agents = filtered_agents[:top_k]

            return filtered_agents
        else:
            logger.error(f"Failed to discover agents: {resp.status_code} - {resp.text}")
            return []