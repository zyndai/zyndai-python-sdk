"""ZyndBase — shared infrastructure for agents and services on the
Zynd network.

Mirrors `zyndai-ts-sdk/src/base.ts`. Owns:
  - identity (Ed25519 keypair, entity_id derivation)
  - A2A server (replaces the legacy Flask webhook)
  - x402 payment processor (kept; orthogonal to comm layer)
  - registry upsert (POST /v1/entities first time, PUT thereafter)
  - heartbeat WebSocket (kept; orthogonal to comm layer)
  - agent-card builder + on-disk card file write

Subclasses (ZyndAIAgent, ZyndService) add execution logic.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Callable, Optional, Type

from pydantic import BaseModel, ConfigDict, Field

from zyndai_agent import dns_registry
from zyndai_agent.a2a.server import A2AServer, Handler, HandlerInput, TaskHandle
from zyndai_agent.ed25519_identity import (
    Ed25519Keypair,
    generate_entity_id,
    sign as ed_sign,
)
from zyndai_agent.entity_card_loader import (
    resolve_card_from_config,
    resolve_keypair,
    build_runtime_card,
)
from zyndai_agent.identity import IdentityManager
from zyndai_agent.payment import X402PaymentProcessor
from zyndai_agent.search import SearchAndDiscoveryManager

logger = logging.getLogger(__name__)


# Pretty-print helpers (best-effort rich, fallback to print).
try:
    from rich.console import Console
    _console = Console()

    def _log(msg: str, style: str = "dim") -> None:
        _console.print(f"  {msg}", style=style)

    def _log_ok(msg: str) -> None:
        _console.print(f"  [bold #8B5CF6]✓[/bold #8B5CF6] {msg}")

    def _log_warn(msg: str) -> None:
        _console.print(f"  [bold yellow]⚠[/bold yellow] {msg}")

    def _log_err(msg: str) -> None:
        _console.print(f"  [bold red]✗[/bold red] {msg}")

    def _log_heartbeat(msg: str) -> None:
        _console.print(f"  [dim #06B6D4]♥[/dim #06B6D4] [dim]{msg}[/dim]")

except ImportError:  # pragma: no cover
    _console = None

    def _log(msg, style=None):  # type: ignore
        print(f"  {msg}")

    def _log_ok(msg):  # type: ignore
        print(f"  ✓ {msg}")

    def _log_warn(msg):  # type: ignore
        print(f"  ⚠ {msg}")

    def _log_err(msg):  # type: ignore
        print(f"  ✗ {msg}")

    def _log_heartbeat(msg):  # type: ignore
        print(f"  ♥ {msg}")


# -----------------------------------------------------------------------------
# Config schema — mirrors TS ZyndBaseConfigSchema
# -----------------------------------------------------------------------------


class SkillConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    name: str
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    examples: Optional[list[str]] = None
    inputModes: Optional[list[str]] = None
    outputModes: Optional[list[str]] = None


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    organization: str
    url: Optional[str] = None


class PricingConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    base_price_usd: float
    currency: str = "USDC"


class ZyndBaseConfig(BaseModel):
    """Base config shared by agents and services."""

    model_config = ConfigDict(extra="allow")

    # Display
    name: str = ""
    description: str = ""
    version: str = "0.1.0"

    # Discovery / search
    category: str = "general"
    tags: Optional[list[str]] = None

    # Network
    registry_url: str = "https://zns01.zynd.ai"
    entity_url: Optional[str] = None

    # A2A server bind
    server_host: str = "0.0.0.0"
    server_port: int = 5000
    a2a_path: str = "/a2a/v1"
    auth_mode: str = "permissive"  # "strict" | "permissive" | "open"

    # Identity
    keypair_path: Optional[str] = None
    config_dir: Optional[str] = None
    developer_keypair_path: Optional[str] = None
    entity_index: Optional[int] = None

    # Card output
    card_output: Optional[str] = None

    # A2A AgentCard fields
    protocol_version: str = "0.3.0"
    provider: Optional[ProviderConfig] = None
    icon_url: Optional[str] = None
    documentation_url: Optional[str] = None
    default_input_modes: Optional[list[str]] = None
    default_output_modes: Optional[list[str]] = None
    capabilities: Optional[dict[str, bool]] = None
    skills: Optional[list[SkillConfig]] = None
    fqan: Optional[str] = None

    # Pricing
    price: Optional[str] = None
    entity_pricing: Optional[PricingConfig] = None

    # Limits
    message_history_limit: int = 100
    max_body_bytes: int = 25 * 1024 * 1024


# -----------------------------------------------------------------------------
# ZyndBase
# -----------------------------------------------------------------------------


_HEARTBEAT_INTERVAL_SECONDS = 30
_HEARTBEAT_RECONNECT_DELAY_SECONDS = 5


class ZyndBase:
    """Base class for Zynd network entities."""

    _entity_label: str = "ZYND ENTITY"
    _entity_type: str = "agent"

    def __init__(
        self,
        config: ZyndBaseConfig,
        *,
        payload_model: Optional[Type[BaseModel]] = None,
        output_model: Optional[Type[BaseModel]] = None,
        max_body_bytes: Optional[int] = None,
    ) -> None:
        self._config = config

        # Default payload model: free-form content + optional attachments.
        from zyndai_agent.payload import AgentPayload
        self.payload_model: Type[BaseModel] = payload_model or AgentPayload
        self.output_model: Optional[Type[BaseModel]] = output_model
        self.max_body_bytes: int = max_body_bytes or config.max_body_bytes

        # Resolve identity.
        self.keypair = self._resolve_keypair(config)
        if not self.keypair:
            raise ValueError(
                "Keypair not found. Set ZYND_AGENT_KEYPAIR_PATH / "
                "ZYND_SERVICE_KEYPAIR_PATH or provide keypair_path in config."
            )

        if self._entity_type == "service":
            self.entity_id = generate_entity_id(self.keypair.public_key_bytes, "service")
        else:
            self.entity_id = self.keypair.entity_id

        # x402 payment.
        self.x402_processor = X402PaymentProcessor(
            ed25519_private_key_bytes=self.keypair.private_key_bytes
        )
        self.pay_to_address = self.x402_processor.account.address

        # Search + identity managers (pre-existing — orthogonal to comm).
        IdentityManager.__init__(self, config.registry_url)  # type: ignore[arg-type]
        self.search = SearchAndDiscoveryManager(registry_url=config.registry_url)

        # Build the card lazily — re-runs per fetch so dynamic fields
        # (timestamps) stay fresh.
        self._static_card = resolve_card_from_config(config)
        self._resolved_provider: Optional[dict[str, Any]] = None

        def _build_card() -> dict[str, Any]:
            base_url = self._get_base_url()
            return build_runtime_card(
                config=config,
                base_url=base_url,
                keypair=self.keypair,
                entity_id=self.entity_id,
                payload_model=self.payload_model,
                output_model=self.output_model,
                fallback_provider=self._resolved_provider,
            )

        self._build_card = _build_card

        # A2A server.
        self.server = A2AServer(
            entity_id=self.entity_id,
            keypair=self.keypair,
            agent_card_builder=_build_card,
            host=config.server_host,
            port=config.server_port,
            a2a_path=config.a2a_path,
            auth_mode=config.auth_mode,  # type: ignore[arg-type]
            max_body_bytes=self.max_body_bytes,
            payload_model=self.payload_model,
            output_model=self.output_model,
            fqan=config.fqan,
        )

        # Heartbeat state.
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._heartbeat_stop = threading.Event()

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    @property
    def a2a_url(self) -> str:
        return self.server.a2a_url

    @property
    def card_url(self) -> str:
        return f"{self._get_base_url()}/.well-known/agent-card.json"

    def install_handler(self, fn: Handler) -> None:
        """Subclasses register their dispatch handler through this. Not
        usually called by user code — see ZyndAIAgent.on_message and
        ZyndService.set_handler / on_message.
        """
        self.server.set_handler(fn)

    def start(self) -> None:
        """Start the A2A server, write the card file, register on the
        registry, start the heartbeat, print the banner.
        """
        self.server.start()
        self._resolve_provider()
        self._write_card_file()
        self._upsert_on_registry()
        self._start_heartbeat()
        self._display_info()

    def stop(self) -> None:
        self._heartbeat_stop.set()
        self.server.stop()

    # -------------------------------------------------------------------------
    # Provider auto-resolve
    # -------------------------------------------------------------------------

    def _resolve_provider(self) -> None:
        try:
            from zyndai_agent.entity_card_loader import resolve_provider_from_developer

            self._resolved_provider = resolve_provider_from_developer(
                registry_url=self._config.registry_url
            )
        except Exception:
            self._resolved_provider = None

    # -------------------------------------------------------------------------
    # Card file
    # -------------------------------------------------------------------------

    def _write_card_file(self) -> None:
        try:
            card = self._build_card()
            card_path = self._config.card_output or os.path.join(
                ".well-known", "agent-card.json"
            )
            card_dir = os.path.dirname(card_path)
            if card_dir:
                os.makedirs(card_dir, exist_ok=True)
            with open(card_path, "w") as f:
                json.dump(card, f, indent=2)
            logger.info(f"Card written to {card_path}")
        except Exception as e:
            logger.debug(f"Could not write card file: {e}")

    # -------------------------------------------------------------------------
    # URL helpers
    # -------------------------------------------------------------------------

    def _get_base_url(self) -> str:
        from zyndai_agent.config_manager import build_entity_url

        url = build_entity_url(self._config)
        if url.endswith("/webhook"):
            url = url[: -len("/webhook")]
        return url.rstrip("/")

    def _is_loopback_url(self, url: str) -> bool:
        try:
            from urllib.parse import urlparse

            host = urlparse(url).hostname or ""
        except Exception:
            return False
        return host in ("localhost", "127.0.0.1", "0.0.0.0", "::1") or host.startswith(
            "127."
        )

    # -------------------------------------------------------------------------
    # Registry upsert
    # -------------------------------------------------------------------------

    def _upsert_on_registry(self) -> None:
        from zyndai_agent.ed25519_identity import (
            create_derivation_proof,
            generate_developer_id,
            load_keypair,
        )

        dev_key_path_str = os.environ.get(
            "ZYND_DEVELOPER_KEYPAIR_PATH",
            str(Path.home() / ".zynd" / "developer.json"),
        )
        dev_key_path = Path(dev_key_path_str)
        has_dev_key = dev_key_path.exists()

        entity_url = self._get_base_url()
        if self._is_loopback_url(entity_url):
            _log_warn(
                f"[registry] entity_url {entity_url} is a loopback address — "
                f"the registry and other agents will not be able to reach this "
                f"{self._entity_type}. Set entity_url to a publicly reachable URL "
                f"before going live."
            )

        try:
            existing = dns_registry.get_entity(self._config.registry_url, self.entity_id)
        except Exception as e:
            _log_warn(f"[registry] lookup failed: {e}")
            return

        # Derive summary from description (matches TS).
        summary = _summarize(self._config.description) or self._config.name

        desired: dict[str, Any] = {
            "name": self._config.name,
            "entity_url": entity_url,
            "category": self._config.category,
            "tags": self._config.tags or [],
            "summary": summary,
        }
        # Service-specific fields.
        if self._entity_type == "service":
            svc_endpoint = getattr(self._config, "service_endpoint", None) or entity_url
            desired["service_endpoint"] = svc_endpoint
            openapi_url = getattr(self._config, "openapi_url", None)
            if openapi_url:
                desired["openapi_url"] = openapi_url

        if existing:
            _log(f"[registry] {self._entity_type} already registered — checking for changes...")
            diff = _compute_update_diff(existing, desired)
            if not diff:
                _log("[registry] no changes — skipping update")
                return
            try:
                dns_registry.update_entity(
                    registry_url=self._config.registry_url,
                    entity_id=self.entity_id,
                    keypair=self.keypair,
                    updates=diff,
                )
                _log_ok(f"[registry] updated {self.entity_id} ({', '.join(diff.keys())})")
            except Exception as e:
                _log_err(f"[registry] update failed: {e}")
            return

        if not has_dev_key:
            _log_warn(
                f"[registry] entity not registered yet and developer keypair not "
                f"found at {dev_key_path} — skipping initial registration. Run "
                f"'zynd auth login --registry <url>' or set "
                f"ZYND_DEVELOPER_KEYPAIR_PATH on the box that owns this entity."
            )
            return

        dev_kp = load_keypair(str(dev_key_path))
        dev_id = generate_developer_id(dev_kp.public_key_bytes)
        entity_index = self._config.entity_index or 0
        proof = create_derivation_proof(dev_kp, self.keypair.public_key_bytes, entity_index)

        _log(f"[registry] registering new {self._entity_type}...")
        try:
            registered_id = dns_registry.register_entity(
                registry_url=self._config.registry_url,
                keypair=self.keypair,
                name=self._config.name,
                entity_url=entity_url,
                category=self._config.category,
                tags=self._config.tags or [],
                summary=summary,
                entity_type=self._entity_type,
                developer_id=dev_id,
                developer_proof=proof,
                entity_pricing=(
                    self._config.entity_pricing.model_dump()
                    if self._config.entity_pricing
                    else None
                ),
            )
            _log_ok(f"[registry] registered {registered_id}")
        except Exception as e:
            msg = str(e)
            if "409" in msg:
                _log_warn(
                    "[registry] register returned 409 (entity already exists) — "
                    "falling back to update..."
                )
                try:
                    dns_registry.update_entity(
                        registry_url=self._config.registry_url,
                        entity_id=self.entity_id,
                        keypair=self.keypair,
                        updates=desired,
                    )
                    _log_ok(f"[registry] updated {self.entity_id}")
                except Exception as e2:
                    _log_err(f"[registry] update failed: {e2}")
                return
            _log_err(f"[registry] register failed: {msg}")

    # -------------------------------------------------------------------------
    # Heartbeat
    # -------------------------------------------------------------------------

    def _start_heartbeat(self) -> None:
        # The pre-existing heartbeat code lives in IdentityManager / a websocket
        # helper. Keep it stable across the A2A migration; just kick it off
        # using whatever the existing implementation expected.
        try:
            self._start_heartbeat_ws()
        except Exception as e:
            _log_warn(f"[heartbeat] failed to start: {e}")

    def _start_heartbeat_ws(self) -> None:
        """Stub — defer to subclass / pre-existing IdentityManager
        heartbeat. The TS SDK runs a Ed25519-signed timestamp every 30s
        over a registry WebSocket. Python equivalent stays in the
        existing implementation; nothing in the A2A migration changes
        the heartbeat protocol.
        """
        # Existing zyndai-agent heartbeat lives in the parent class.
        # If a subclass / mixin provides _start_heartbeat_thread, use it.
        impl = getattr(self, "_legacy_start_heartbeat", None)
        if callable(impl):
            impl(self._config.registry_url)

    # -------------------------------------------------------------------------
    # Display
    # -------------------------------------------------------------------------

    def _display_info(self) -> None:
        name = self._config.name or "Unnamed"
        price = (
            self._config.price
            or (
                f"${self._config.entity_pricing.base_price_usd} "
                f"{self._config.entity_pricing.currency}"
                if self._config.entity_pricing
                else "Free"
            )
        )
        print()
        print("  " + "=" * 56)
        print(f"  {self._entity_label}")
        print("  " + "=" * 56)
        print()
        print(f"  Name         {name}")
        if self._config.description:
            print(f"  Description  {self._config.description}")
        print(f"  ID           {self.entity_id}")
        print(f"  Public Key   {self.keypair.public_key_string}")
        print(f"  Address      {self.pay_to_address}")
        print(f"  A2A          {self.a2a_url}")
        print(f"  Card         {self.card_url}")
        print(f"  Price        {price}")
        print()

    # -------------------------------------------------------------------------
    # Keypair resolver
    # -------------------------------------------------------------------------

    @staticmethod
    def _resolve_keypair(config: ZyndBaseConfig) -> Optional[Ed25519Keypair]:
        env_path = (
            os.environ.get("ZYND_AGENT_KEYPAIR_PATH")
            or os.environ.get("ZYND_SERVICE_KEYPAIR_PATH")
        )
        if env_path:
            cfg_path = env_path
        else:
            cfg_path = config.keypair_path  # may be None
        try:
            return resolve_keypair(
                keypair_path=cfg_path, config_dir=config.config_dir
            )
        except (ValueError, FileNotFoundError):
            return None


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _summarize(text: Optional[str], max_len: int = 160) -> str:
    """Derive a search-snippet summary from description. Cuts at the
    first sentence boundary or 160 chars, whichever is shorter.
    Mirrors the TS SDK's `summarize()` behavior.
    """
    if not text:
        return ""
    collapsed = " ".join(text.split()).strip()
    if not collapsed:
        return ""
    if len(collapsed) <= max_len:
        return collapsed
    import re

    m = re.match(r"^(.{20,160}?[.!?])(\s|$)", collapsed)
    if m:
        return m.group(1).strip()
    return collapsed[: max_len - 1].rstrip() + "…"


def _compute_update_diff(
    existing: dict[str, Any], desired: dict[str, Any]
) -> dict[str, Any]:
    """Return only the fields whose values differ. Same logic as TS
    `computeUpdateDiff`.
    """
    diff: dict[str, Any] = {}
    for key, want in desired.items():
        have = existing.get(key)
        if key == "tags":
            want_tags = want if (isinstance(want, list) and want) else []
            have_tags = have if (isinstance(have, list) and have) else []
            if want_tags != have_tags:
                diff[key] = want
        else:
            if want != have:
                diff[key] = want
    return diff
