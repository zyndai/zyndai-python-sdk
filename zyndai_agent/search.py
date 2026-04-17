# Agent Discovery and Search Protocol Module for ZyndAI
import logging
import requests

from typing import List, Optional, TypedDict

from zyndai_agent import dns_registry


logging.basicConfig(
    level=logging.ERROR,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("SearchAndDiscovery")


class AgentSearchResponse(TypedDict):
    """Search result from agent-dns registry."""
    entity_id: str
    name: str
    summary: str
    category: str
    tags: list
    capability_summary: Optional[dict]
    entity_url: str
    home_registry: str
    score: float
    score_breakdown: Optional[dict]
    card: Optional[dict]  # if enrich=true
    status: Optional[str]
    last_heartbeat: Optional[str]


class SearchAndDiscoveryManager:
    """
    Search and discovery protocol for ZyndAI agents using agent-dns registry.

    Uses POST /v1/search with rich query body for semantic and structured search.
    """

    def __init__(self, registry_url: str = "http://localhost:8080"):
        self.agents = []
        self.registry_url = registry_url

    def search_entities(
        self,
        keyword: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        skills: Optional[List[str]] = None,
        protocols: Optional[List[str]] = None,
        languages: Optional[List[str]] = None,
        models: Optional[List[str]] = None,
        min_trust_score: Optional[float] = None,
        limit: int = 10,
        federated: bool = False,
        enrich: bool = False,
    ) -> List[AgentSearchResponse]:
        """
        Search for agents using the agent-dns POST /v1/search endpoint.

        Args:
            keyword: Semantic search query
            category: Filter by agent category
            tags: Filter by tags
            skills: Filter by skills/capabilities
            protocols: Filter by supported protocols
            languages: Filter by supported languages
            models: Filter by AI models used
            min_trust_score: Minimum trust score filter
            limit: Maximum number of results (default: 10)
            federated: Whether to search federated registries
            enrich: Whether to include Agent Card data in results

        Returns:
            List of matching agents
        """
        logger.info(f"Searching agents with query='{keyword}', skills={skills}")

        result = dns_registry.search_entities(
            registry_url=self.registry_url,
            query=keyword,
            category=category,
            tags=tags,
            skills=skills,
            protocols=protocols,
            languages=languages,
            models=models,
            min_trust_score=min_trust_score,
            max_results=limit,
            federated=federated,
            enrich=enrich,
        )

        agents = result.get("results", [])
        total = result.get("total_found", len(agents))
        logger.info(f"Found {len(agents)} agents (total: {total}).")
        return agents

    def search_agents_by_capabilities(
        self,
        capabilities: Optional[List[str]] = None,
        top_k: Optional[int] = None
    ) -> List[AgentSearchResponse]:
        """
        Discover agents based on capabilities.
        Capabilities are passed as 'skills' in the search request.

        Args:
            capabilities: List of capability terms to search for
            top_k: Maximum number of results to return

        Returns:
            List of matching agents
        """
        if capabilities is None:
            capabilities = []
        logger.info(f"Discovering agents by capabilities: {capabilities}")

        # Convert capabilities to both query and skills
        keyword = " ".join(capabilities) if capabilities else None
        limit = top_k if top_k is not None else 10

        return self.search_entities(
            keyword=keyword,
            skills=capabilities if capabilities else None,
            limit=limit,
        )

    def search_agents_by_keyword(
        self,
        keyword: str,
        limit: int = 10,
    ) -> List[AgentSearchResponse]:
        """
        Search for agents using a semantic keyword search.

        Args:
            keyword: Search term for semantic matching
            limit: Maximum number of results (default: 10)

        Returns:
            List of matching agents
        """
        return self.search_entities(keyword=keyword, limit=limit)

    def get_agent_by_id(self, entity_id: str) -> Optional[AgentSearchResponse]:
        """
        Get a specific agent by its ID.

        Args:
            entity_id: The agent ID (agdns:... format)

        Returns:
            Agent details or None if not found
        """
        return dns_registry.get_entity(self.registry_url, entity_id)

    def get_entity_card(self, entity_id: str) -> Optional[dict]:
        """
        Fetch an agent's Agent Card.

        Args:
            entity_id: The agent ID

        Returns:
            Agent Card dict or None
        """
        return dns_registry.get_entity_card(self.registry_url, entity_id)
