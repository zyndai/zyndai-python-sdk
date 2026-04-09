"""
DNS Registry Client for agent-dns decentralized registry.

Encapsulates all agent-dns HTTP API interactions, replacing calls
to the old centralized NestJS registry.
"""

import json
import logging
import requests
from typing import Optional, List

from zyndai_agent.ed25519_identity import Ed25519Keypair, sign

logger = logging.getLogger(__name__)


def register_agent(
    registry_url: str,
    keypair: Ed25519Keypair,
    name: str,
    agent_url: str,
    category: str = "general",
    tags: Optional[List[str]] = None,
    summary: Optional[str] = None,
    capability_summary: Optional[dict] = None,
    developer_id: Optional[str] = None,
    developer_proof: Optional[dict] = None,
    agent_name: Optional[str] = None,
    version: Optional[str] = None,
) -> str:
    """
    Register an agent on the agent-dns mesh.

    Builds signable payload matching server.go:422-429, signs with agent's
    Ed25519 key, and POSTs to /v1/agents.

    Returns:
        agent_id: The registered agent's ID
    """
    # Build signable payload (sorted keys to match Go's json.Marshal)
    signable = {
        "agent_url": agent_url,
        "category": category,
        "name": name,
        "public_key": keypair.public_key_string,
        "summary": summary or "",
        "tags": tags or [],
    }
    signable_bytes = json.dumps(signable, sort_keys=True, separators=(",", ":")).encode()
    signature = sign(keypair.private_key, signable_bytes)

    # Build full registration request
    body = {
        "name": name,
        "agent_url": agent_url,
        "category": category,
        "tags": tags or [],
        "summary": summary or "",
        "public_key": keypair.public_key_string,
        "signature": signature,
    }

    if capability_summary:
        body["capability_summary"] = capability_summary
    if developer_id:
        body["developer_id"] = developer_id
    if developer_proof:
        body["developer_proof"] = developer_proof
    if agent_name:
        body["agent_name"] = agent_name
    if version:
        body["version"] = version

    resp = requests.post(
        f"{registry_url}/v1/agents",
        json=body,
        headers={"Content-Type": "application/json"},
    )

    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f"Failed to register agent on agent-dns. "
            f"Status: {resp.status_code}, Response: {resp.text}"
        )

    data = resp.json()
    return data.get("agent_id", keypair.agent_id)


def check_handle_available(registry_url: str, handle: str) -> dict:
    """
    Check if a developer handle/username is available on the registry.
    GET /v1/handles/{handle}/available

    Returns:
        dict with keys: handle, available (bool), reason (optional)
    """
    try:
        resp = requests.get(f"{registry_url}/v1/handles/{handle}/available")
        if resp.status_code == 200:
            return resp.json()
        return {"handle": handle, "available": False, "reason": f"HTTP {resp.status_code}"}
    except requests.RequestException as e:
        return {"handle": handle, "available": False, "reason": str(e)}


def check_agent_name_available(
    registry_url: str, developer_handle: str, agent_name: str
) -> dict:
    """
    Check if an agent name is available under a developer handle.
    GET /v1/names/{developer}/{agent}/available

    Returns:
        dict with keys: developer, agent_name, available (bool), reason (optional),
                        existing_agent_id (optional)
    """
    try:
        resp = requests.get(
            f"{registry_url}/v1/names/{developer_handle}/{agent_name}/available"
        )
        if resp.status_code == 200:
            return resp.json()
        return {
            "developer": developer_handle,
            "agent_name": agent_name,
            "available": False,
            "reason": f"HTTP {resp.status_code}",
        }
    except requests.RequestException as e:
        return {
            "developer": developer_handle,
            "agent_name": agent_name,
            "available": False,
            "reason": str(e),
        }


def get_agent(registry_url: str, agent_id: str) -> Optional[dict]:
    """
    Look up an agent by ID.
    GET /v1/agents/{agent_id}
    """
    try:
        resp = requests.get(f"{registry_url}/v1/agents/{agent_id}")
        if resp.status_code == 200:
            return resp.json()
        logger.error(f"Failed to get agent {agent_id}: {resp.status_code}")
        return None
    except requests.RequestException as e:
        logger.error(f"Request failed: {e}")
        return None


def update_agent(
    registry_url: str,
    agent_id: str,
    keypair: Ed25519Keypair,
    updates: dict,
) -> bool:
    """
    Update an agent's registration.
    PUT /v1/agents/{agent_id} with Authorization: Bearer ed25519:<sig>

    The server verifies the Bearer signature against the request body bytes.
    Adds a body-level signature over the update fields, then signs the full
    body for the Authorization header.
    """
    # Sign the update content (excluding signature field itself)
    signable_bytes = json.dumps(updates, sort_keys=True, separators=(",", ":")).encode()
    updates["signature"] = sign(keypair.private_key, signable_bytes)

    # Serialize full body (with signature) and sign for auth header
    body_bytes = json.dumps(updates, sort_keys=True, separators=(",", ":")).encode()
    auth_sig = sign(keypair.private_key, body_bytes)

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {auth_sig}",
    }

    try:
        resp = requests.put(
            f"{registry_url}/v1/agents/{agent_id}",
            data=body_bytes,
            headers=headers,
        )
        if resp.status_code == 200:
            return True
        logger.error(f"Failed to update agent {agent_id}: {resp.status_code} {resp.text}")
        return False
    except requests.RequestException as e:
        logger.error(f"Request failed: {e}")
        return False


def delete_agent(
    registry_url: str,
    agent_id: str,
    keypair: Ed25519Keypair,
) -> bool:
    """
    Delete an agent's registration.
    DELETE /v1/agents/{agent_id} with Authorization: Bearer ed25519:<sig>
    """
    auth_sig = sign(keypair.private_key, agent_id.encode())
    headers = {
        "Authorization": f"Bearer {auth_sig}",
    }

    try:
        resp = requests.delete(
            f"{registry_url}/v1/agents/{agent_id}",
            headers=headers,
        )
        if resp.status_code in (200, 204):
            return True
        logger.error(f"Failed to delete agent {agent_id}: {resp.status_code}")
        return False
    except requests.RequestException as e:
        logger.error(f"Request failed: {e}")
        return False


def search_agents(
    registry_url: str,
    query: Optional[str] = None,
    category: Optional[str] = None,
    tags: Optional[List[str]] = None,
    skills: Optional[List[str]] = None,
    protocols: Optional[List[str]] = None,
    languages: Optional[List[str]] = None,
    models: Optional[List[str]] = None,
    min_trust_score: Optional[float] = None,
    status: Optional[str] = None,
    developer_id: Optional[str] = None,
    developer_handle: Optional[str] = None,
    fqan: Optional[str] = None,
    max_results: int = 10,
    offset: int = 0,
    federated: bool = False,
    enrich: bool = False,
    timeout_ms: Optional[int] = None,
) -> dict:
    """
    Search for agents via POST /v1/search.

    Supports all SearchRequest filters from the registry API:
    - query: Free-text search
    - category: Filter by category
    - tags: Filter by tags
    - skills: Filter by capability skills (e.g., ["code-review"])
    - protocols: Filter by protocols (e.g., ["a2a", "mcp"])
    - languages: Filter by languages (e.g., ["python"])
    - models: Filter by AI models (e.g., ["gpt-4"])
    - min_trust_score: Minimum trust score (0.0-1.0)
    - status: Filter by status ("active", "inactive", "any")
    - developer_id: Filter by developer
    - max_results: Maximum results to return
    - offset: Pagination offset
    - federated: Search across federated peers
    - enrich: Include full Agent Card in results
    - timeout_ms: Federated search timeout

    Returns:
        dict with keys: results, total_found, offset, has_more, search_stats
    """
    body = {}

    if query:
        body["query"] = query
    if category:
        body["category"] = category
    if tags:
        body["tags"] = tags
    if skills:
        body["skills"] = skills
    if protocols:
        body["protocols"] = protocols
    if languages:
        body["languages"] = languages
    if models:
        body["models"] = models
    if min_trust_score is not None:
        body["min_trust_score"] = min_trust_score
    if status:
        body["status"] = status
    if developer_id:
        body["developer_id"] = developer_id
    if developer_handle:
        body["developer_handle"] = developer_handle
    if fqan:
        body["fqan"] = fqan
    if offset:
        body["offset"] = offset
    if timeout_ms is not None:
        body["timeout_ms"] = timeout_ms
    body["max_results"] = max_results
    body["federated"] = federated
    body["enrich"] = enrich

    try:
        resp = requests.post(
            f"{registry_url}/v1/search",
            json=body,
            headers={"Content-Type": "application/json"},
        )

        if resp.status_code == 200:
            return resp.json()
        else:
            logger.error(f"Search failed: {resp.status_code} - {resp.text}")
            return {"results": [], "total_found": 0, "has_more": False}

    except requests.RequestException as e:
        logger.error(f"Search request failed: {e}")
        return {"results": [], "total_found": 0, "has_more": False}


def get_agent_card(registry_url: str, agent_id: str) -> Optional[dict]:
    """
    Fetch an agent's Agent Card.
    GET /v1/agents/{agent_id}/card
    """
    try:
        resp = requests.get(f"{registry_url}/v1/agents/{agent_id}/card")
        if resp.status_code == 200:
            return resp.json()
        logger.error(f"Failed to get agent card for {agent_id}: {resp.status_code}")
        return None
    except requests.RequestException as e:
        logger.error(f"Request failed: {e}")
        return None


def get_categories(registry_url: str) -> List[str]:
    """
    Get all available agent categories.
    GET /v1/categories
    """
    try:
        resp = requests.get(f"{registry_url}/v1/categories")
        if resp.status_code == 200:
            data = resp.json()
            return data.get("categories", [])
        logger.error(f"Failed to get categories: {resp.status_code}")
        return []
    except requests.RequestException as e:
        logger.error(f"Request failed: {e}")
        return []


def get_tags(registry_url: str) -> List[str]:
    """
    Get popular agent tags.
    GET /v1/tags
    """
    try:
        resp = requests.get(f"{registry_url}/v1/tags")
        if resp.status_code == 200:
            data = resp.json()
            return data.get("tags", [])
        logger.error(f"Failed to get tags: {resp.status_code}")
        return []
    except requests.RequestException as e:
        logger.error(f"Request failed: {e}")
        return []


def get_registry_info(registry_url: str) -> Optional[dict]:
    """
    Get registry discovery info.
    GET /v1/info

    Returns:
        dict with keys: registry_id, name, developer_onboarding (mode, auth_url)
    """
    try:
        resp = requests.get(f"{registry_url}/v1/info", timeout=10)
        if resp.status_code == 200:
            return resp.json()
        logger.error(f"Failed to get registry info: {resp.status_code}")
        return None
    except requests.RequestException as e:
        logger.error(f"Request failed: {e}")
        return None


def get_agent_fqan(registry_url: str, agent_id: str) -> Optional[str]:
    """
    Look up the FQAN (Fully Qualified Agent Name) for an agent.
    Checks if the agent has a ZNS name binding and returns the FQAN string
    (e.g., "dns01.zynd.ai/acme-corp/doc-translator").

    Returns None if the agent has no name binding.
    """
    try:
        # Search for the agent to get FQAN from search results
        resp = requests.post(
            f"{registry_url}/v1/search",
            json={"query": agent_id, "max_results": 1, "enrich": False},
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("results", [])
            for r in results:
                if r.get("agent_id") == agent_id:
                    fqan = r.get("fqan", "")
                    if fqan:
                        return fqan
        return None
    except requests.RequestException:
        return None


def get_network_status(registry_url: str) -> Optional[dict]:
    """
    Get registry node status.
    GET /v1/network/status
    """
    try:
        resp = requests.get(f"{registry_url}/v1/network/status", timeout=10)
        if resp.status_code == 200:
            return resp.json()
        logger.error(f"Failed to get network status: {resp.status_code}")
        return None
    except requests.RequestException as e:
        logger.error(f"Request failed: {e}")
        return None
