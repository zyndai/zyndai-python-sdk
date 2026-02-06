# Agent Discovery and Search Protocol Module for ZyndAI
import logging
import requests
from urllib.parse import urlencode

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
    httpWebhookUrl: Optional[str]  # Field for webhook communication
    inboxTopic: Optional[str]
    capabilities: Optional[dict]
    status: Optional[str]
    didIdentifier: str
    did: str  # JSON string of DID credential


class SearchAndDiscoveryManager:
    """
    This class implements the search and discovery protocol for ZyndAI agents.
    It allows agents to discover each other and share information about their capabilities.

    The search uses semantic matching via the keyword parameter, allowing for
    fuzzy/vague searches across agent names, descriptions, and capabilities.
    """

    def __init__(self, registry_url: str = "http://localhost:3002"):
        self.agents = []
        self.registry_url = registry_url

    def search_agents(
        self,
        keyword: Optional[str] = None,
        name: Optional[str] = None,
        capabilities: Optional[List[str]] = None,
        status: Optional[str] = None,
        did: Optional[str] = None,
        limit: int = 10,
        offset: int = 0
    ) -> List[AgentSearchResponse]:
        """
        Search for agents in the registry using various filters.

        The keyword parameter supports semantic search across name, description,
        capabilities, and metadata fields.

        Args:
            keyword: Semantic search term (searches across name, description, capabilities, metadata)
            name: Filter by agent name (case-insensitive, partial match)
            capabilities: List of capabilities to filter by
            status: Filter by agent status (e.g., "ACTIVE")
            did: Filter by exact DID match
            limit: Maximum number of results to return (default: 10, max: 100)
            offset: Number of results to skip for pagination (default: 0)

        Returns:
            List of matching agents
        """
        logger.info(f"Searching agents with keyword='{keyword}', capabilities={capabilities}")

        # Build query parameters
        params = {}

        if keyword:
            params["keyword"] = keyword
        if name:
            params["name"] = name
        if capabilities:
            params["capabilities"] = ",".join(capabilities)
        if status:
            params["status"] = status
        if did:
            params["did"] = did

        params["limit"] = limit
        params["offset"] = offset

        try:
            url = f"{self.registry_url}/agents"
            logger.info(f"GET {url}?{urlencode(params)}")

            resp = requests.get(url, params=params)

            if resp.status_code == 200:
                response_data = resp.json()
                # API returns { data: [...], count: N, total: N }
                agents = response_data.get("data", [])
                total = response_data.get("total", len(agents))
                logger.info(f"Found {len(agents)} agents (total: {total}).")
                return agents
            else:
                logger.error(f"Failed to search agents: {resp.status_code} - {resp.text}")
                return []

        except requests.RequestException as e:
            logger.error(f"Request failed: {e}")
            return []

    def search_agents_by_capabilities(
        self,
        capabilities: List[str] = [],
        top_k: Optional[int] = None
    ) -> List[AgentSearchResponse]:
        """
        Discover agents based on capabilities using semantic keyword search.

        This method converts capabilities into a keyword search query for
        semantic matching across the registry.

        Args:
            capabilities: List of capability terms to search for
            top_k: Maximum number of results to return

        Returns:
            List of matching agents
        """
        logger.info(f"Discovering agents by capabilities: {capabilities}")

        # Convert capabilities list into a semantic search keyword
        # Join capabilities into a search phrase for semantic matching
        keyword = " ".join(capabilities) if capabilities else None

        limit = top_k if top_k is not None else 10

        return self.search_agents(
            keyword=keyword,
            limit=limit
        )

    def search_agents_by_keyword(
        self,
        keyword: str,
        limit: int = 10,
        offset: int = 0
    ) -> List[AgentSearchResponse]:
        """
        Search for agents using a semantic keyword search.

        The keyword is matched against agent name, description, capabilities,
        and metadata using semantic search.

        Args:
            keyword: Search term for semantic matching
            limit: Maximum number of results (default: 10)
            offset: Pagination offset (default: 0)

        Returns:
            List of matching agents
        """
        return self.search_agents(keyword=keyword, limit=limit, offset=offset)

    def get_agent_by_id(self, agent_id: str) -> Optional[AgentSearchResponse]:
        """
        Get a specific agent by its ID.

        Args:
            agent_id: The unique identifier of the agent

        Returns:
            Agent details or None if not found
        """
        try:
            url = f"{self.registry_url}/agents/{agent_id}"
            resp = requests.get(url)

            if resp.status_code == 200:
                return resp.json()
            else:
                logger.error(f"Failed to get agent {agent_id}: {resp.status_code}")
                return None

        except requests.RequestException as e:
            logger.error(f"Request failed: {e}")
            return None
