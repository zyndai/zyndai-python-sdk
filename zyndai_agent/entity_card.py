"""
Agent Card Module for agent-dns decentralized registry.

Builds, signs, and serves the Agent Card JSON matching
agent-dns/internal/models/agent_card.go.
"""

import json
import time
from typing import Optional

from zyndai_agent.ed25519_identity import Ed25519Keypair, sign


def build_endpoints(base_url: str) -> dict:
    """
    Build the standard endpoint dict from a base URL.

    Args:
        base_url: The agent's base URL (e.g. "https://example.com")

    Returns:
        dict with invoke, invoke_async, health, and agent_card URLs
    """
    base = base_url.rstrip("/")
    return {
        "invoke": f"{base}/webhook/sync",
        "invoke_async": f"{base}/webhook",
        "health": f"{base}/health",
        "agent_card": f"{base}/.well-known/agent.json",
    }


def build_entity_card(
    entity_id: str,
    name: str,
    description: str,
    entity_url: str,
    keypair: Ed25519Keypair,
    capabilities: Optional[dict] = None,
    price: Optional[str] = None,
    version: str = "1.0",
) -> dict:
    """
    Build an Agent Card dict matching the AgentCard struct in agent-dns.

    Args:
        entity_id: The agent's agdns: ID
        name: Agent display name
        description: Agent description
        entity_url: Base URL where the agent is hosted
        keypair: Ed25519 keypair for the agent
        capabilities: Dict like {"ai": ["nlp"], "protocols": ["http"]}
        price: Price string like "$0.01"
        version: Card version string

    Returns:
        dict: Agent Card matching agent-dns format
    """
    # Build capabilities array from flat dict
    capability_list = []
    if capabilities:
        for category, items in capabilities.items():
            if isinstance(items, list):
                for item in items:
                    capability_list.append({
                        "name": item,
                        "category": category,
                    })
            else:
                capability_list.append({
                    "name": str(items),
                    "category": category,
                })

    # Build endpoints
    # Strip trailing slash from entity_url
    base_url = entity_url.rstrip("/")
    # If entity_url already ends with /webhook, derive base from it
    if base_url.endswith("/webhook"):
        base_url = base_url[: -len("/webhook")]

    endpoints = build_endpoints(base_url)

    # Build pricing
    pricing = None
    if price:
        # Parse price string like "$0.01"
        amount = price.lstrip("$")
        try:
            rate = float(amount)
        except ValueError:
            rate = 0.0

        pricing = {
            "model": "per-request",
            "currency": "USDC",
            "rates": {"default": rate},
            "payment_methods": ["x402"],
        }

    now = time.time()
    iso_now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))

    card = {
        "entity_id": entity_id,
        "name": name,
        "description": description,
        "public_key": keypair.public_key_string,
        "entity_url": base_url,
        "version": version,
        "status": "online",
        "capabilities": capability_list,
        "endpoints": endpoints,
        "last_heartbeat": iso_now,
        "signed_at": iso_now,
    }

    if pricing:
        card["pricing"] = pricing

    return card


def sign_entity_card(card_dict: dict, keypair: Ed25519Keypair) -> dict:
    """
    Sign an Agent Card and set the 'signature' field.
    Matches card/fetch.go:110-134: sign canonical JSON without signature field.

    Args:
        card_dict: The Agent Card dict (without signature)
        keypair: Ed25519 keypair to sign with

    Returns:
        dict: The Agent Card with 'signature' field added
    """
    # Remove existing signature if present for signing
    card_copy = {k: v for k, v in card_dict.items() if k != "signature"}

    # Canonical JSON: sorted keys, no extra whitespace
    canonical = json.dumps(card_copy, sort_keys=True, separators=(",", ":")).encode()

    signature = sign(keypair.private_key, canonical)

    card_dict["signature"] = signature
    return card_dict


def agent_card_flask_route(card_builder):
    """
    Returns a Flask route handler for GET /.well-known/agent.json.

    Args:
        card_builder: Callable that returns a signed Agent Card dict

    Returns:
        A Flask view function
    """
    from flask import jsonify

    def well_known_agent_card():
        card = card_builder()
        return jsonify(card), 200

    return well_known_agent_card
