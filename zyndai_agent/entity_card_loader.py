"""
Agent Card Loader Module.

Handles loading agent cards from .well-known/agent.json files,
resolving keypairs, building runtime cards, and computing card hashes
for change detection during self-registration.
"""

import base64
import hashlib
import json
import os
import time
from typing import Optional

from zyndai_agent.ed25519_identity import (
    Ed25519Keypair,
    keypair_from_private_bytes,
    load_keypair,
    load_keypair_with_metadata,
    sign,
)
from zyndai_agent.entity_card import sign_entity_card, build_endpoints


CARD_HASH_FIELDS = ("name", "description", "capabilities", "category", "tags", "pricing", "summary")


def load_entity_card(path: str) -> dict:
    """
    Read and validate an agent card JSON file.

    Args:
        path: Path to the .well-known/agent.json file

    Returns:
        dict: The parsed card data

    Raises:
        FileNotFoundError: If the card file doesn't exist
        ValueError: If the card file is invalid
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Agent card file not found: {path}")

    with open(path, "r") as f:
        card = json.load(f)

    if not isinstance(card, dict):
        raise ValueError(f"Agent card must be a JSON object, got {type(card).__name__}")

    if not card.get("name"):
        raise ValueError("Agent card must have a 'name' field")

    return card


def resolve_keypair(agent_config) -> Ed25519Keypair:
    """
    Resolve the agent keypair using the priority chain:

    1. ZYND_AGENT_KEYPAIR_PATH env var
    2. ZYND_AGENT_PRIVATE_KEY + ZYND_AGENT_PUBLIC_KEY env vars (base64)
    3. agent_config.keypair_path constructor argument
    4. Fallback: .agent/config.json private_key field

    Args:
        agent_config: AgentConfig instance

    Returns:
        Ed25519Keypair

    Raises:
        ValueError: If no keypair can be resolved
    """
    # 1. ZYND_AGENT_KEYPAIR_PATH env var
    env_path = os.environ.get("ZYND_AGENT_KEYPAIR_PATH")
    if env_path:
        expanded = os.path.expanduser(env_path)
        return load_keypair(expanded)

    # 2. ZYND_AGENT_PRIVATE_KEY + ZYND_AGENT_PUBLIC_KEY env vars
    env_priv = os.environ.get("ZYND_AGENT_PRIVATE_KEY")
    if env_priv:
        private_bytes = base64.b64decode(env_priv)
        return keypair_from_private_bytes(private_bytes)

    # 3. agent_config.keypair_path
    keypair_path = getattr(agent_config, "keypair_path", None)
    if keypair_path:
        expanded = os.path.expanduser(keypair_path)
        return load_keypair(expanded)

    # 4. Fallback: .agent/config.json
    config_dir = getattr(agent_config, "config_dir", None) or ".agent"
    config_path = os.path.join(os.getcwd(), config_dir, "config.json")
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            config = json.load(f)
        private_key_b64 = config.get("private_key")
        if private_key_b64:
            private_bytes = base64.b64decode(private_key_b64)
            return keypair_from_private_bytes(private_bytes)

    raise ValueError(
        "No keypair found. Set ZYND_AGENT_KEYPAIR_PATH env var, "
        "pass keypair_path to AgentConfig, or run 'zynd keys derive --index 0'."
    )


def build_runtime_card(
    static_card: dict,
    base_url: str,
    keypair: Ed25519Keypair,
    payload_model=None,
    output_model=None,
    max_file_size_bytes: Optional[int] = None,
) -> dict:
    """
    Merge a static card (from file) with runtime fields to produce a serveable card.

    Adds: entity_id, public_key, endpoints, status, timestamps, signature.
    Strips: server, registry sections (SDK-internal).

    Args:
        static_card: The card dict loaded from file
        base_url: The resolved base URL (after ngrok etc.)
        keypair: The agent's Ed25519 keypair

    Returns:
        dict: The complete, signed runtime card
    """
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    card = {}

    # Add runtime identity fields.
    card["entity_id"] = keypair.entity_id
    card["public_key"] = keypair.public_key_string

    # Copy metadata fields from static card
    for key in ("name", "description", "version", "category", "tags",
                "summary", "capabilities", "pricing"):
        if key in static_card:
            card[key] = static_card[key]

    # Set defaults
    card.setdefault("version", "1.0")

    # Build absolute endpoints
    card["entity_url"] = base_url
    card["endpoints"] = build_endpoints(base_url)

    # Runtime timestamps
    card["status"] = "online"
    card["last_heartbeat"] = now
    card["signed_at"] = now

    # Advertise the request payload schema (JSON Schema) so callers can
    # discover what fields + attachment types this agent accepts.
    if payload_model is not None:
        from zyndai_agent.payload import build_payload_card_fields
        card.update(build_payload_card_fields(payload_model, output_model))
        if max_file_size_bytes is not None:
            card["max_file_size_bytes"] = max_file_size_bytes

    # Sign the card
    return sign_entity_card(card, keypair)


def compute_card_hash(card: dict) -> str:
    """
    Compute a SHA-256 hash of the metadata fields for change detection.

    Only hashes fields that a developer would edit (name, description,
    capabilities, category, tags, pricing, summary).

    Args:
        card: The card dict (either static or runtime)

    Returns:
        str: Hex-encoded SHA-256 hash
    """
    hashable = {}
    for field in CARD_HASH_FIELDS:
        if field in card:
            hashable[field] = card[field]

    canonical = json.dumps(hashable, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def resolve_card_from_config(agent_config) -> dict:
    """
    Bridge function: build a card dict from legacy AgentConfig fields.

    This allows the self-registration flow to work with both
    card-file and legacy AgentConfig approaches.

    Args:
        agent_config: AgentConfig instance with name, description, etc.

    Returns:
        dict: A card dict with the same structure as a loaded card file
    """
    card = {
        "name": agent_config.name,
        "description": agent_config.description,
        "version": "1.0",
        "category": getattr(agent_config, "category", "general"),
    }

    tags = getattr(agent_config, "tags", None)
    if tags:
        card["tags"] = tags

    summary = getattr(agent_config, "summary", None)
    if summary:
        card["summary"] = summary

    # Convert capabilities from legacy dict format to list format
    capabilities = getattr(agent_config, "capabilities", None)
    if capabilities and isinstance(capabilities, dict):
        cap_list = []
        for category, items in capabilities.items():
            if isinstance(items, list):
                for item in items:
                    cap_list.append({"name": item, "category": category})
            else:
                cap_list.append({"name": str(items), "category": category})
        card["capabilities"] = cap_list
    elif capabilities:
        card["capabilities"] = capabilities

    # Use entity_pricing from config if provided, otherwise derive from price string
    entity_pricing = getattr(agent_config, "entity_pricing", None)
    if entity_pricing and isinstance(entity_pricing, dict):
        card["pricing"] = entity_pricing
    else:
        price = getattr(agent_config, "price", None)
        if price:
            amount = price.lstrip("$")
            try:
                rate = float(amount)
            except ValueError:
                rate = 0.0
            card["pricing"] = {
                "model": "per-request",
                "currency": "USDC",
                "rates": {"default": rate},
                "payment_methods": ["x402"],
            }

    # Add server section from config
    card["server"] = {
        "host": getattr(agent_config, "webhook_host", "0.0.0.0"),
        "port": getattr(agent_config, "webhook_port", 5000),
        "public_url": getattr(agent_config, "webhook_url", None),
        "use_ngrok": getattr(agent_config, "use_ngrok", False),
    }

    card["registry"] = {
        "url": getattr(agent_config, "registry_url", "https://dns01.zynd.ai"),
    }

    return card


def load_derivation_metadata(keypair_path: str) -> Optional[dict]:
    """
    Read derivation metadata from a keypair JSON file.

    If the keypair was created via 'zynd keys derive', it will contain
    a 'derived_from' field with developer_public_key and index.

    Args:
        keypair_path: Path to the keypair JSON file

    Returns:
        dict with developer_public_key and index, or None if not a derived key
    """
    expanded = os.path.expanduser(keypair_path)
    if not os.path.exists(expanded):
        return None

    with open(expanded, "r") as f:
        data = json.load(f)

    return data.get("derived_from")
