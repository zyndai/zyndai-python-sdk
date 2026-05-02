"""Card / config loader.

Two roles:
  1. Resolve the agent's keypair from environment / config / disk.
  2. Build the A2A-shaped agent card from a ZyndBaseConfig at runtime
     (delegates to `zyndai_agent.a2a.card.build_agent_card`).

Mirrors `zyndai-ts-sdk/src/entity-card-loader.ts` post-A2A migration.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any, Optional, Type

from pydantic import BaseModel

from zyndai_agent.ed25519_identity import (
    Ed25519Keypair,
    generate_developer_id,
    keypair_from_private_bytes,
    load_keypair,
)

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Keypair resolution
# -----------------------------------------------------------------------------


def resolve_keypair(
    keypair_path: Optional[str] = None,
    config_dir: Optional[str] = None,
    *,
    agent_config: Any = None,
) -> Ed25519Keypair:
    """Resolve the agent keypair using the priority chain:

      1. ZYND_AGENT_KEYPAIR_PATH env  → keypair JSON file
      2. ZYND_AGENT_PRIVATE_KEY env   → base64 private key bytes
      3. keypair_path arg             → keypair JSON file
      4. .agent/config.json in cwd    → reads private_key field (base64)

    Both kwargs (new style) and `agent_config` object (legacy) supported.
    """
    if agent_config is not None:
        keypair_path = keypair_path or getattr(agent_config, "keypair_path", None)
        config_dir = config_dir or getattr(agent_config, "config_dir", None)

    env_path = os.environ.get("ZYND_AGENT_KEYPAIR_PATH") or os.environ.get(
        "ZYND_SERVICE_KEYPAIR_PATH"
    )
    if env_path:
        return load_keypair(os.path.expanduser(env_path))

    env_priv = os.environ.get("ZYND_AGENT_PRIVATE_KEY")
    if env_priv:
        private_bytes = base64.b64decode(env_priv)
        return keypair_from_private_bytes(private_bytes)

    if keypair_path:
        return load_keypair(os.path.expanduser(keypair_path))

    config_dir = config_dir or ".agent"
    config_path = os.path.join(os.getcwd(), config_dir, "config.json")
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            data = json.load(f)
        private_key_b64 = data.get("private_key")
        if private_key_b64:
            private_bytes = base64.b64decode(private_key_b64)
            return keypair_from_private_bytes(private_bytes)

    raise ValueError(
        "No keypair found. Set ZYND_AGENT_KEYPAIR_PATH env var, "
        "pass keypair_path, or ensure .agent/config.json exists with a "
        "private_key field."
    )


def load_derivation_metadata(keypair_path: str) -> Optional[dict[str, Any]]:
    """Read a keypair JSON file and return the `derived_from` block, or None."""
    if not os.path.exists(keypair_path):
        return None
    try:
        with open(keypair_path, "r") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"Failed to read keypair at {keypair_path}: {e}") from e
    df = data.get("derived_from")
    if df and isinstance(df, dict):
        return df
    return None


# -----------------------------------------------------------------------------
# Provider auto-resolve from developer key + registry
# -----------------------------------------------------------------------------


def resolve_provider_from_developer(
    *,
    registry_url: str,
    developer_keypair_path: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Resolve the AgentCard `provider` block from the developer's identity.

    Reads the local developer keypair (~/.zynd/developer.json by
    default), derives the developer_id, fetches the developer record
    from the registry, and builds:
      - organization: dev_handle if claimed, else dev name
      - url:          home_registry from the registry record

    Returns None on missing developer key, missing registry record, or
    network failure (non-fatal so agent startup isn't blocked).

    Mirrors `resolveProviderFromDeveloper` in the TS SDK.
    """
    from zyndai_agent.dns_registry import get_developer

    key_path = developer_keypair_path or os.environ.get(
        "ZYND_DEVELOPER_KEYPAIR_PATH"
    ) or os.path.join(os.path.expanduser("~"), ".zynd", "developer.json")

    if not os.path.exists(key_path):
        return None

    try:
        dev_kp = load_keypair(key_path)
    except Exception:
        return None

    developer_id = generate_developer_id(dev_kp.public_key_bytes)
    record = get_developer(registry_url, developer_id)
    if record is None:
        return None

    handle = record.get("dev_handle")
    name = record.get("name")
    home_registry = record.get("home_registry")

    organization = handle or name
    if not organization:
        return None

    provider: dict[str, Any] = {"organization": organization}
    if home_registry:
        provider["url"] = (
            home_registry if home_registry.startswith("http") else f"https://{home_registry}"
        )
    return provider


# -----------------------------------------------------------------------------
# Card building (delegated to a2a/card.py)
# -----------------------------------------------------------------------------


def resolve_card_from_config(config: Any) -> dict[str, Any]:
    """Static-card fields shaken out of the config. Used for hashing
    and for card building. Returns a dict; the A2A card builder
    composes the wire shape from this + runtime fields.
    """
    return {
        "name": getattr(config, "name", "") or "",
        "description": getattr(config, "description", "") or "",
        "version": getattr(config, "version", "0.1.0") or "0.1.0",
        "category": getattr(config, "category", "general") or "general",
        "tags": getattr(config, "tags", None) or [],
    }


def build_runtime_card(
    *,
    config: Any,
    base_url: str,
    keypair: Ed25519Keypair,
    entity_id: str,
    payload_model: Optional[Type[BaseModel]] = None,
    output_model: Optional[Type[BaseModel]] = None,
    fallback_provider: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build the A2A-shaped agent card directly from a ZyndBaseConfig
    + runtime identity. Fully signed and ready to ship over the wire.

    Provider precedence: config.provider (when its `organization` is
    non-empty) > fallback_provider (resolved from developer.json +
    registry) > omitted.
    """
    from zyndai_agent.a2a.card import (
        AgentCardCapabilities,
        AgentCardProvider,
        AgentCardSkill,
        BuildCardOptions,
        build_agent_card,
    )

    # Pricing — convert from PricingConfig to the card-shaped dict.
    pricing = None
    if getattr(config, "entity_pricing", None) is not None:
        ep = config.entity_pricing
        currency = getattr(ep, "currency", "USDC") or "USDC"
        base_price = getattr(ep, "base_price_usd", 0)
        pricing = {
            "model": "per-request",
            "currency": currency,
            "rates": {"default": base_price},
            "paymentMethods": ["x402"],
        }
    elif getattr(config, "price", None):
        # Parse "$0.01" / "$0.01 USDC".
        s = config.price.lstrip("$").split()
        try:
            amount = float(s[0])
        except (ValueError, IndexError):
            amount = 0.0
        currency = s[1] if len(s) > 1 else "USDC"
        pricing = {
            "model": "per-request",
            "currency": currency,
            "rates": {"default": amount},
            "paymentMethods": ["x402"],
        }

    # Pick provider: config wins when its organization is non-empty.
    provider_obj: Optional[AgentCardProvider] = None
    cfg_provider = getattr(config, "provider", None)
    if (
        cfg_provider is not None
        and getattr(cfg_provider, "organization", None)
        and str(cfg_provider.organization).strip()
    ):
        provider_obj = AgentCardProvider(
            organization=cfg_provider.organization,
            url=getattr(cfg_provider, "url", None),
        )
    elif fallback_provider:
        provider_obj = AgentCardProvider(
            organization=fallback_provider.get("organization", ""),
            url=fallback_provider.get("url"),
        )

    # Skills — convert from SkillConfig list to AgentCardSkill list.
    skills_in = getattr(config, "skills", None)
    skills_out: Optional[list[AgentCardSkill]] = None
    if skills_in:
        skills_out = []
        for s in skills_in:
            sd = s if isinstance(s, dict) else s.model_dump(exclude_none=True)  # type: ignore[union-attr]
            skills_out.append(
                AgentCardSkill(
                    id=sd["id"],
                    name=sd["name"],
                    description=sd.get("description"),
                    tags=sd.get("tags"),
                    examples=sd.get("examples"),
                    inputModes=sd.get("inputModes"),
                    outputModes=sd.get("outputModes"),
                )
            )

    capabilities = None
    cfg_caps = getattr(config, "capabilities", None)
    if cfg_caps:
        capabilities = AgentCardCapabilities(
            streaming=cfg_caps.get("streaming"),
            pushNotifications=cfg_caps.get("pushNotifications"),
            stateTransitionHistory=cfg_caps.get("stateTransitionHistory"),
        )

    registry_host = _registry_host_from_url(getattr(config, "registry_url", ""))

    opts = BuildCardOptions(
        name=getattr(config, "name", "") or "",
        description=getattr(config, "description", "") or "",
        version=getattr(config, "version", "0.1.0") or "0.1.0",
        base_url=base_url,
        keypair=keypair,
        entity_id=entity_id,
        protocol_version=getattr(config, "protocol_version", "0.3.0") or "0.3.0",
        a2a_path=getattr(config, "a2a_path", "/a2a/v1") or "/a2a/v1",
        provider=provider_obj,
        icon_url=getattr(config, "icon_url", None),
        documentation_url=getattr(config, "documentation_url", None),
        capabilities=capabilities,
        default_input_modes=getattr(config, "default_input_modes", None),
        default_output_modes=getattr(config, "default_output_modes", None),
        skills=skills_out,
        payload_model=payload_model,
        output_model=output_model,
        fqan=getattr(config, "fqan", None),
        registry=registry_host,
        pricing=pricing,
        category=getattr(config, "category", None),
        tags=getattr(config, "tags", None),
    )
    return build_agent_card(opts)


def compute_card_hash(card: dict[str, Any]) -> str:
    """Stable SHA-256 of the metadata fields that define content
    identity. Used by registry upsert flows to detect drift.
    """
    import hashlib
    from zyndai_agent.a2a.canonical import canonical_bytes

    fields = ("name", "description", "version", "category", "tags", "pricing", "summary")
    subset = {k: card.get(k) for k in fields}
    return hashlib.sha256(canonical_bytes(subset)).hexdigest()


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def load_entity_card(path: str) -> dict[str, Any]:
    """Read and validate an agent-card JSON file."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Agent card file not found: {path}")
    with open(path, "r") as f:
        card = json.load(f)
    if not isinstance(card, dict):
        raise ValueError(
            f"Agent card must be a JSON object, got {type(card).__name__}"
        )
    if not card.get("name"):
        raise ValueError("Agent card must have a 'name' field")
    return card


def _registry_host_from_url(url: str) -> Optional[str]:
    try:
        from urllib.parse import urlparse

        host = urlparse(url).netloc
        return host or None
    except Exception:
        return None
