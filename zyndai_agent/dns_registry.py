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


def register_entity(
    registry_url: str,
    keypair: Ed25519Keypair,
    name: str,
    entity_url: str,
    category: str = "general",
    tags: Optional[List[str]] = None,
    summary: Optional[str] = None,
    capability_summary: Optional[dict] = None,
    developer_id: Optional[str] = None,
    developer_proof: Optional[dict] = None,
    entity_name: Optional[str] = None,
    version: Optional[str] = None,
    entity_type: Optional[str] = None,
    service_endpoint: Optional[str] = None,
    openapi_url: Optional[str] = None,
    entity_pricing: Optional[dict] = None,
) -> str:
    """
    Register an agent or service on the registry.

    Builds signable payload, signs with Ed25519 key, and POSTs to /v1/agents
    (or /v1/services for type=service).

    Returns:
        entity_id: The registered agent/service ID
    """
    # Build signable payload. Must produce a byte sequence IDENTICAL to what
    # the Go backend computes in handleRegisterEntity (server.go), which
    # marshals a map[string]interface{} via json.Marshal:
    #   - sort_keys=True           → Go json.Marshal sorts map keys alphabetically
    #   - separators=(",", ":")    → Go json.Marshal has no whitespace
    #   - ensure_ascii=False       → Go json.Marshal writes raw UTF-8 bytes
    #                                for non-ASCII (em-dash, arrow, etc.);
    #                                Python's default ensure_ascii=True would
    #                                escape them to \uXXXX and produce a
    #                                different byte sequence, leading to
    #                                HTTP 401 "invalid agent signature".
    signable = {
        "entity_url": entity_url or "",
        "category": category,
        "name": name,
        "public_key": keypair.public_key_string,
        "summary": summary or "",
        "tags": tags or [],
    }
    if entity_type:
        signable["entity_type"] = entity_type
    signable_bytes = json.dumps(
        signable, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    signature = sign(keypair.private_key, signable_bytes)

    # Build full registration request
    body = {
        "name": name,
        "entity_url": entity_url or "",
        "category": category,
        "tags": tags or [],
        "summary": summary or "",
        "public_key": keypair.public_key_string,
        "signature": signature,
    }

    if entity_type:
        body["entity_type"] = entity_type
    if service_endpoint:
        body["service_endpoint"] = service_endpoint
    if openapi_url:
        body["openapi_url"] = openapi_url
    if entity_pricing:
        body["entity_pricing"] = entity_pricing
    if capability_summary:
        body["capability_summary"] = capability_summary
    if developer_id:
        body["developer_id"] = developer_id
    if developer_proof:
        body["developer_proof"] = developer_proof
    if entity_name:
        body["entity_name"] = entity_name
    if version:
        body["version"] = version

    # Use unified /v1/entities endpoint
    endpoint = "/v1/entities"
    resp = requests.post(
        f"{registry_url}{endpoint}",
        json=body,
        headers={"Content-Type": "application/json"},
    )

    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f"Failed to register on registry. "
            f"Status: {resp.status_code}, Response: {resp.text}"
        )

    data = resp.json()
    return data.get("entity_id") or keypair.entity_id


def get_entity(
    registry_url: str, entity_id: str, entity_type: Optional[str] = None
) -> Optional[dict]:
    """
    Look up an entity by ID.
    GET /v1/entities/{entity_id}
    """
    label = entity_type or "entity"
    try:
        resp = requests.get(f"{registry_url}/v1/entities/{entity_id}")
        if resp.status_code == 200:
            return resp.json()
        logger.error(f"Failed to get {label} {entity_id}: {resp.status_code}")
        return None
    except requests.RequestException as e:
        logger.error(f"Request failed: {e}")
        return None


def get_developer(registry_url: str, developer_id: str) -> Optional[dict]:
    """Fetch a developer record by developer_id (zns:dev:<hash>).

    Used by base.py to auto-populate the agent card's `provider` block
    from the developer keypair on disk + the registry's developer record.
    Returns None on 404 / network error.
    """
    try:
        resp = requests.get(f"{registry_url}/v1/developers/{developer_id}")
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code != 404:
            logger.warning(f"get_developer {developer_id}: HTTP {resp.status_code}")
        return None
    except requests.RequestException as e:
        logger.warning(f"get_developer request failed: {e}")
        return None


def update_entity(
    registry_url: str,
    entity_id: str,
    keypair: Ed25519Keypair,
    updates: dict,
    entity_type: Optional[str] = None,
) -> bool:
    """
    Update an entity's registration.
    PUT /v1/entities/{entity_id} with Authorization: Bearer ed25519:<sig>

    The server verifies the Bearer signature against the request body bytes.
    Adds a body-level signature over the update fields, then signs the full
    body for the Authorization header.
    """
    label = entity_type or "entity"
    # Canonical JSON encoding must match Go's json.Marshal default output
    # byte-for-byte — see register_entity() above for the full rationale.
    # The critical bit is ensure_ascii=False so non-ASCII chars (em-dash,
    # arrow, emoji, etc.) in the update body serialize as raw UTF-8 and not
    # as Python's default \uXXXX escapes, which would make the Go verifier
    # compute a different signature and return HTTP 401.
    signable_bytes = json.dumps(
        updates, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    updates["signature"] = sign(keypair.private_key, signable_bytes)

    # Serialize full body (with signature) and sign for auth header
    body_bytes = json.dumps(
        updates, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    auth_sig = sign(keypair.private_key, body_bytes)

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {auth_sig}",
    }

    try:
        resp = requests.put(
            f"{registry_url}/v1/entities/{entity_id}",
            data=body_bytes,
            headers=headers,
        )
        if resp.status_code == 200:
            return True
        logger.error(
            f"Failed to update {label} {entity_id}: {resp.status_code} {resp.text}"
        )
        return False
    except requests.RequestException as e:
        logger.error(f"Request failed: {e}")
        return False
    except requests.RequestException as e:
        logger.error(f"Request failed: {e}")
        return False


def delete_entity(
    registry_url: str,
    entity_id: str,
    keypair: Ed25519Keypair,
    entity_type: Optional[str] = None,
) -> bool:
    """
    Delete an entity's registration.
    DELETE /v1/entities/{entity_id} with Authorization: Bearer ed25519:<sig>
    """
    label = entity_type or "entity"
    auth_sig = sign(keypair.private_key, entity_id.encode())
    headers = {
        "Authorization": f"Bearer {auth_sig}",
    }

    try:
        resp = requests.delete(
            f"{registry_url}/v1/entities/{entity_id}",
            headers=headers,
        )
        if resp.status_code in (200, 204):
            return True
        logger.error(f"Failed to delete {label} {entity_id}: {resp.status_code}")
        return False
    except requests.RequestException as e:
        logger.error(f"Request failed: {e}")
        return False


def search_entities(
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
    entity_type: Optional[str] = None,
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
    if entity_type:
        body["entity_type"] = entity_type
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


def get_entity_card(
    registry_url: str, entity_id: str, entity_type: Optional[str] = None
) -> Optional[dict]:
    """
    Fetch an entity's Card.
    GET /v1/entities/{entity_id}/card
    """
    label = entity_type or "entity"
    try:
        resp = requests.get(f"{registry_url}/v1/entities/{entity_id}/card")
        if resp.status_code == 200:
            return resp.json()
        logger.error(f"Failed to get {label} card for {entity_id}: {resp.status_code}")
        return None
    except requests.RequestException as e:
        logger.error(f"Request failed: {e}")
        return None


def get_entity_fqan(registry_url: str, entity_id: str) -> Optional[str]:
    """
    Look up the FQAN (Fully Qualified Agent Name) for an agent.
    Checks if the agent has a ZNS name binding and returns the FQAN string
    (e.g., "zns01.zynd.ai/acme-corp/doc-translator").

    Returns None if the agent has no name binding.
    """
    try:
        # Search for the agent to get FQAN from search results
        resp = requests.post(
            f"{registry_url}/v1/search",
            json={"query": entity_id, "max_results": 1, "enrich": False},
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("results", [])
            for r in results:
                if r.get("entity_id") == entity_id:
                    fqan = r.get("fqan", "")
                    if fqan:
                        return fqan
        return None
    except requests.RequestException:
        return None


