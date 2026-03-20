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
    """
    # Sign the agent_id as auth token
    auth_sig = sign(keypair.private_key, agent_id.encode())
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {auth_sig}",
    }

    try:
        resp = requests.put(
            f"{registry_url}/v1/agents/{agent_id}",
            json=updates,
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
    max_results: int = 10,
    federated: bool = False,
    enrich: bool = False,
) -> dict:
    """
    Search for agents via POST /v1/search with SearchRequest body.
    Matches models/search.go:4-21.

    Returns:
        dict with keys: results (list), total_found (int), has_more (bool)
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
