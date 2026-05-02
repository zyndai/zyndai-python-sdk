"""Public surface of the Zynd Python SDK.

Mirrors `zyndai-ts-sdk/src/index.ts` post-A2A migration:
  - ZyndAIAgent / ZyndService run on the A2A blueprint (no more legacy webhook).
  - The A2A wire types, signing, and client are reachable as a sub-namespace
    via ``from zyndai_agent import a2a`` or via the explicit
    ``A2AClient`` / ``A2AServer`` re-exports below.
"""

from zyndai_agent.base import ZyndBase, ZyndBaseConfig
from zyndai_agent.agent import ZyndAIAgent, AgentConfig
from zyndai_agent.service import ZyndService, ServiceConfig
from zyndai_agent.message import AgentMessage
from zyndai_agent.payload import AgentPayload, Attachment
from zyndai_agent.search import SearchAndDiscoveryManager, AgentSearchResponse
from zyndai_agent.identity import IdentityManager
from zyndai_agent.payment import X402PaymentProcessor
from zyndai_agent.config_manager import (
    ConfigManager,
    build_entity_url,
    load_home_registry_url,
    resolve_registry_url,
)
from zyndai_agent.ed25519_identity import (
    Ed25519Keypair,
    generate_keypair,
    keypair_from_private_bytes,
)
from zyndai_agent.entity_card_loader import (
    load_entity_card,
    resolve_keypair,
    build_runtime_card,
    compute_card_hash,
    resolve_card_from_config,
    load_derivation_metadata,
    resolve_provider_from_developer,
)
from zyndai_agent import dns_registry as DNSRegistryClient
from zyndai_agent import a2a
from zyndai_agent.a2a.client import A2AClient
from zyndai_agent.a2a.server import A2AServer

__all__ = [
    # Core
    "ZyndBase",
    "ZyndBaseConfig",
    "ZyndAIAgent",
    "AgentConfig",
    "ZyndService",
    "ServiceConfig",
    # Messaging / payloads
    "AgentMessage",
    "AgentPayload",
    "Attachment",
    # Discovery / identity / payments
    "SearchAndDiscoveryManager",
    "AgentSearchResponse",
    "IdentityManager",
    "X402PaymentProcessor",
    # Config / identity
    "ConfigManager",
    "build_entity_url",
    "load_home_registry_url",
    "resolve_registry_url",
    "Ed25519Keypair",
    "generate_keypair",
    "keypair_from_private_bytes",
    # Card
    "load_entity_card",
    "resolve_keypair",
    "build_runtime_card",
    "compute_card_hash",
    "resolve_card_from_config",
    "load_derivation_metadata",
    "resolve_provider_from_developer",
    # Registry / A2A
    "DNSRegistryClient",
    "a2a",
    "A2AClient",
    "A2AServer",
]
