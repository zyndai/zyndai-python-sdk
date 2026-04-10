# Agent Discovery and Search Protocol Module for ZyndAI
import logging
import requests

from typing import Any, Dict, List, Optional, TypedDict

from zyndai_agent import dns_registry


logging.basicConfig(
    level=logging.ERROR,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("SearchAndDiscovery")


class AgentSearchResponse(TypedDict):
    """Search result from agent-dns registry."""
    agent_id: str
    name: str
    summary: str
    category: str
    tags: list
    capability_summary: Optional[dict]
    agent_url: str
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

    def search_agents(
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
        entity_type: Optional[str] = None,
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

        result = dns_registry.search_agents(
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
            entity_type=entity_type,
        )

        agents = result.get("results", [])
        total = result.get("total_found", len(agents))
        logger.info(f"Found {len(agents)} agents (total: {total}).")
        return agents

    def search_agents_by_capabilities(
        self,
        capabilities: List[str] = [],
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
        logger.info(f"Discovering agents by capabilities: {capabilities}")

        # Convert capabilities to both query and skills
        keyword = " ".join(capabilities) if capabilities else None
        limit = top_k if top_k is not None else 10

        return self.search_agents(
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
        return self.search_agents(keyword=keyword, limit=limit)

    def get_agent_by_id(self, agent_id: str) -> Optional[AgentSearchResponse]:
        """
        Get a specific agent by its ID.

        Args:
            agent_id: The agent ID (agdns:... format)

        Returns:
            Agent details or None if not found
        """
        return dns_registry.get_agent(self.registry_url, agent_id)

    def search_services(
        self,
        keyword: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        skills: Optional[List[str]] = None,
        limit: int = 10,
    ) -> List[AgentSearchResponse]:
        """Search for services (not agents). Sets entity_type='service' automatically."""
        return self.search_agents(
            keyword=keyword, category=category, tags=tags,
            skills=skills, limit=limit, entity_type="service",
        )

    def call_service(
        self,
        service_id: str,
        method: str = "GET",
        path: str = "",
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
        timeout: int = 30,
    ) -> Dict[str, Any]:
        """Call a registered service by its ID. Auto-uses x402 for paid services."""
        service = dns_registry.get_agent(self.registry_url, service_id)
        if not service:
            raise ValueError(f"Service not found: {service_id}")

        endpoint = service.get("service_endpoint") or service.get("entity_url") or service.get("agent_url")
        if not endpoint:
            raise ValueError(f"Service '{service.get('name')}' has no endpoint URL")

        url = endpoint.rstrip("/") + path
        session = getattr(self, "x402_processor", None)
        http = session.session if session and hasattr(session, "session") else requests

        resp = http.request(
            method=method.upper(), url=url, params=params,
            json=body if method.upper() in ("POST", "PUT", "PATCH") else None,
            timeout=timeout,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Service '{service.get('name')}' returned {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()
        except Exception:
            return {"raw": resp.text}

    def use_service(
        self,
        keyword: str,
        method: str = "GET",
        path: str = "",
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
        category: Optional[str] = None,
        timeout: int = 30,
    ) -> Dict[str, Any]:
        """Discover a service by keyword and call it in one step."""
        services = self.search_services(keyword=keyword, category=category, limit=1)
        if not services:
            raise ValueError(f"No service found matching '{keyword}'")
        service = services[0]
        logger.info(f"Using service: {service.get('name')} ({service.get('agent_id')})")
        return self.call_service(
            service_id=service.get("agent_id"),
            method=method, path=path, params=params, body=body, timeout=timeout,
        )

    def get_agent_card(self, agent_id: str) -> Optional[dict]:
        """
        Fetch an agent's Agent Card.

        Args:
            agent_id: The agent ID

        Returns:
            Agent Card dict or None
        """
        return dns_registry.get_agent_card(self.registry_url, agent_id)
