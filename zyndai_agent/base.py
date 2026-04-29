"""
ZyndBase — Shared infrastructure for agents and services on the Zynd network.

Handles identity (keypair), webhook communication, heartbeat, card serving,
and payment processing. Subclasses (ZyndAIAgent, ZyndService) add execution logic.
"""

import json
import logging
import os
import threading
import time
from pathlib import Path

from pydantic import BaseModel
from typing import Optional, List

from zyndai_agent.search import SearchAndDiscoveryManager
from zyndai_agent.identity import IdentityManager
from zyndai_agent.webhook_communication import WebhookCommunicationManager
from zyndai_agent.payment import X402PaymentProcessor
from zyndai_agent.ed25519_identity import Ed25519Keypair
from zyndai_agent.entity_card_loader import (
    resolve_keypair,
    build_runtime_card,
    resolve_card_from_config,
)
from zyndai_agent import dns_registry

logger = logging.getLogger(__name__)

try:
    from rich.console import Console
    _console = Console()
    def _log(msg: str, style: str = "dim"):
        _console.print(f"  {msg}", style=style)
    def _log_ok(msg: str):
        _console.print(f"  [bold #8B5CF6]✓[/bold #8B5CF6] {msg}")
    def _log_warn(msg: str):
        _console.print(f"  [bold yellow]⚠[/bold yellow] {msg}")
    def _log_err(msg: str):
        _console.print(f"  [bold red]✗[/bold red] {msg}")
    def _log_heartbeat(msg: str):
        _console.print(f"  [dim #06B6D4]♥[/dim #06B6D4] [dim]{msg}[/dim]")
except ImportError:
    _console = None
    def _log(msg, style=None): print(f"  {msg}")
    def _log_ok(msg): print(f"  ✓ {msg}")
    def _log_warn(msg): print(f"  ⚠ {msg}")
    def _log_err(msg): print(f"  ✗ {msg}")
    def _log_heartbeat(msg): print(f"  ♥ {msg}")


class ZyndBaseConfig(BaseModel):
    """Base config shared by agents and services."""
    name: str = ""
    description: str = ""
    capabilities: Optional[dict] = None

    auto_reconnect: bool = True
    message_history_limit: int = 100
    registry_url: str = "http://localhost:8080"

    # Webhook — bind config inside the process
    webhook_host: Optional[str] = "0.0.0.0"
    webhook_port: Optional[int] = 5000

    # Public URL advertised to the registry and used to build the Agent
    # Card at /.well-known/zynd-agent.json. When set, this takes precedence
    # over host/port derivation. Used by hosting layers (e.g. zynd-deployer)
    # to inject an HTTPS URL while the container still binds to 0.0.0.0:5000.
    entity_url: Optional[str] = None

    # Deprecated: use `entity_url` instead. Retained for one release for
    # backward compatibility; `_build_entity_url` emits a warning when only
    # this field is set.
    webhook_url: Optional[str] = None

    # Registry fields
    category: str = "general"
    tags: Optional[List[str]] = None
    summary: Optional[str] = None

    # Ngrok
    use_ngrok: bool = False
    ngrok_auth_token: Optional[str] = None

    # Pricing
    price: Optional[str] = None
    entity_pricing: Optional[dict] = None

    # Identity
    keypair_path: Optional[str] = None
    config_dir: Optional[str] = None
    card_output: Optional[str] = None


class ZyndBase(
    SearchAndDiscoveryManager,
    IdentityManager,
    WebhookCommunicationManager,
):
    """
    Base class for Zynd network entities (agents and services).

    Provides: identity resolution, webhook server, heartbeat, card serving,
    x402 payments, search/discovery. Subclasses add execution logic.
    """

    # Subclasses override this for display
    _entity_label: str = "ZYND ENTITY"
    _entity_type: str = "agent"

    def __init__(self, config: ZyndBaseConfig, payload_model=None, output_model=None, max_file_size_bytes=None):
        self._config = config
        self._static_card = None

        # The payload model defines what incoming request bodies look like. If
        # the developer doesn't supply one we fall back to the default
        # AgentPayload (free-form content + optional attachments) so behavior
        # matches legacy agents.
        from zyndai_agent.payload import AgentPayload
        self.payload_model = payload_model or AgentPayload
        self.output_model = output_model
        self.max_file_size_bytes = max_file_size_bytes

        # Resolve keypair
        self.keypair = self._resolve_keypair(config)
        if not self.keypair:
            raise ValueError(
                "Keypair not found. Set ZYND_AGENT_KEYPAIR_PATH / ZYND_SERVICE_KEYPAIR_PATH "
                "or provide keypair_path in config."
            )

        # Canonical entity ID (agent-flavor "zns:<hash>" or service-flavor "zns:svc:<hash>").
        if self._entity_type == "service":
            from zyndai_agent.ed25519_identity import generate_entity_id
            self.entity_id = generate_entity_id(self.keypair.public_key_bytes, "service")
        else:
            self.entity_id = self.keypair.entity_id

        # x402 payment
        self.x402_processor = X402PaymentProcessor(
            ed25519_private_key_bytes=self.keypair.private_key_bytes
        )
        self.pay_to_address = self.x402_processor.account.address

        # Init parent classes
        IdentityManager.__init__(self, config.registry_url)
        SearchAndDiscoveryManager.__init__(self, registry_url=config.registry_url)

        # Build card
        self._static_card = resolve_card_from_config(config)

        def _build_card():
            if not self.keypair:
                return {}
            base_url = self._get_base_url()
            card = build_runtime_card(
                self._static_card,
                base_url,
                self.keypair,
                payload_model=self.payload_model,
                output_model=self.output_model,
                max_file_size_bytes=self.max_file_size_bytes,
            )
            if self._entity_type == "service":
                card["entity_type"] = "service"
            return card

        self._build_card = _build_card

        # Derive the x402 runtime price string from structured entity_pricing
        # if the operator only filled in the registry-facing field. Contract:
        #   - If price is already set, use it verbatim (operator override wins).
        #   - Else if entity_pricing has a positive base_price_usd, format it
        #     as "${amount} {currency}" so WebhookCommunicationManager can
        #     charge per call via x402.
        #   - Else leave it unset (free service).
        # This exists so users can specify pricing ONCE in config.json under
        # entity_pricing and have both the registry registration AND runtime
        # charging pick it up automatically.
        runtime_price = config.price
        if not runtime_price and config.entity_pricing:
            base = config.entity_pricing.get("base_price_usd")
            if isinstance(base, (int, float)) and base > 0:
                currency = config.entity_pricing.get("currency") or "USDC"
                runtime_price = f"${base} {currency}"

        # Resolve the public-facing URL, honoring the new `entity_url` field
        # (preferred) and the deprecated `webhook_url` alias. When either is
        # explicitly set we hand the "/webhook"-suffixed shape straight to
        # WebhookCommunicationManager; when BOTH are unset we leave it None
        # so the server-start path can still auto-derive using the actual
        # bound port (important if port 5000 is taken and the server falls
        # through to 5001/5002/etc. during local dev).
        _public_webhook_url = config.webhook_url
        if getattr(config, "entity_url", None):
            _public_webhook_url = f"{config.entity_url.rstrip('/')}/webhook"

        # Start webhook server
        WebhookCommunicationManager.__init__(
            self,
            entity_id=self.entity_id,
            webhook_host=config.webhook_host,
            webhook_port=config.webhook_port,
            webhook_url=_public_webhook_url,
            auto_restart=config.auto_reconnect,
            message_history_limit=config.message_history_limit,
            identity_credential=None,
            keypair=self.keypair,
            agent_card_builder=_build_card,
            price=runtime_price,
            pay_to_address=self.pay_to_address,
            use_ngrok=config.use_ngrok,
            ngrok_auth_token=config.ngrok_auth_token or os.environ.get("NGROK_AUTH_TOKEN"),
            max_file_size_bytes=self.max_file_size_bytes,
        )

        # Write card to disk
        self._write_card_file()

        # Auto-register / update entity on the registry before starting the
        # heartbeat so the WS endpoint exists when the first ping fires.
        self._upsert_on_registry()

        # Start heartbeat
        self._heartbeat_thread = None
        self._heartbeat_stop = threading.Event()
        if self.keypair:
            self._start_heartbeat(config.registry_url)

        # Display info
        self._display_info()

    @staticmethod
    def _resolve_keypair(config) -> Optional[Ed25519Keypair]:
        """Resolve keypair from env vars or config.keypair_path."""
        # Check service-specific env var first
        env_path = os.environ.get("ZYND_SERVICE_KEYPAIR_PATH")
        if env_path:
            config.keypair_path = env_path
        try:
            return resolve_keypair(config)
        except ValueError:
            return None

    def _get_base_url(self) -> str:
        """Return the public base URL used by the Agent Card builder.

        Delegates to ``_build_entity_url`` so the precedence
        (``entity_url`` → deprecated ``webhook_url`` → host/port derivation)
        stays in one place. Falls back to the WebhookCommunicationManager's
        resolved ``webhook_url`` if the config-level helper returns nothing,
        which can happen on the very first call before config propagation.
        """
        from zyndai_agent.config_manager import _build_entity_url

        try:
            url = _build_entity_url(self._config) or ""
        except Exception:
            url = ""
        if not url:
            url = getattr(self, "webhook_url", None) or ""
        if url.endswith("/webhook"):
            return url[: -len("/webhook")]
        return url.rstrip("/")

    def _write_card_file(self):
        """Write .well-known/agent.json to disk."""
        try:
            card = self._build_card()
            if not card:
                return
            card_path = self._config.card_output or os.path.join(".well-known", "agent.json")
            card_dir = os.path.dirname(card_path)
            if card_dir:
                os.makedirs(card_dir, exist_ok=True)
            with open(card_path, "w") as f:
                json.dump(card, f, indent=2)
            logger.info(f"Card written to {card_path}")
        except Exception as e:
            logger.debug(f"Could not write card file: {e}")

    @staticmethod
    def _compute_update_diff(
        existing: dict,
        desired: dict,
    ) -> dict:
        """Return only the fields in *desired* that differ from *existing*.

        Tags comparison: None / [] are both treated as "no tags" so a
        registry that returns ``null`` for an empty tag list never triggers a
        spurious PUT when local config has ``[]``.

        Uses plain ``==`` for all comparisons (values are primitives + lists,
        so this is fine without deep-copy or JSON round-tripping).
        """
        diff: dict = {}
        for key, want in desired.items():
            have = existing.get(key)
            if key == "tags":
                want_tags = want if (isinstance(want, list) and len(want) > 0) else []
                have_tags = have if (isinstance(have, list) and len(have) > 0) else []
                if want_tags != have_tags:
                    diff[key] = want
            else:
                if want != have:
                    diff[key] = want
        return diff

    def _upsert_on_registry(self) -> None:
        """Auto-register or update this entity on the registry at startup.

        Mirrors TS ZyndBase.upsertOnRegistry() (src/base.ts:176-317).

        Steps:
        1. Locate developer keypair (ZYND_DEVELOPER_KEYPAIR_PATH or ~/.zynd/developer.json).
           If missing → warn and return (don't crash; containerised deploys that only
           ship the agent key still start webhook + heartbeat).
        2. Derive developer_id and HD proof from dev keypair.
        3. GET registry — if exists, PUT update; if missing, POST register.
        4. On HTTP 409 (GET/POST race) fall back to PUT update.
        5. On any other failure → log red error but do NOT raise.
        """
        from zyndai_agent.ed25519_identity import (
            load_keypair,
            generate_developer_id,
            create_derivation_proof,
        )

        # 1. Locate developer keypair (presence only — load happens later if needed).
        # Entity self-update only requires the entity keypair (dual-key auth on
        # the registry). Dev key is required only for first-time registration.
        dev_key_path_str = os.environ.get("ZYND_DEVELOPER_KEYPAIR_PATH")
        if not dev_key_path_str:
            dev_key_path_str = str(Path.home() / ".zynd" / "developer.json")
        dev_key_path = Path(dev_key_path_str)
        has_dev_key = dev_key_path.exists()

        # 3. Build registration fields
        entity_url = self._get_base_url()
        entity_pricing = self._config.entity_pricing or None

        # Loopback warning (mirrors TS isLoopbackUrl check)
        _loopback_hosts = ("localhost", "127.0.0.1", "::1", "0.0.0.0")
        if any(h in entity_url for h in _loopback_hosts):
            _log_warn(
                f"[registry] entity_url {entity_url!r} is a loopback address — "
                f"the registry and other agents will not be able to reach this "
                f"{self._entity_type}. Set entity_url to a publicly reachable URL "
                "(ngrok, cloudflared, or a real domain) before going live."
            )

        # Service-specific fields: service_endpoint defaults to entity_url
        service_endpoint: Optional[str] = None
        openapi_url: Optional[str] = None
        if self._entity_type == "service":
            service_endpoint = (
                getattr(self._config, "service_endpoint", None) or entity_url
            )
            openapi_url = getattr(self._config, "openapi_url", None)

        # 4. Check whether the entity already exists
        try:
            existing = dns_registry.get_entity(
                self._config.registry_url, self.entity_id
            )
        except Exception as e:
            _log_warn(f"[registry] lookup failed: {e}")
            return

        # Desired fields — the full set we'd like the registry to have
        desired_fields: dict = {
            "name": self._config.name,
            "entity_url": entity_url,
            "category": self._config.category,
            "tags": self._config.tags or [],
            "summary": self._config.summary or "",
        }
        if service_endpoint:
            desired_fields["service_endpoint"] = service_endpoint
        if openapi_url:
            desired_fields["openapi_url"] = openapi_url

        def _try_update(update_fields: dict) -> bool:
            ok = dns_registry.update_entity(
                registry_url=self._config.registry_url,
                entity_id=self.entity_id,
                keypair=self.keypair,
                updates=update_fields,
            )
            if ok:
                changed_keys = ", ".join(update_fields.keys())
                _log_ok(f"[registry] updated {self.entity_id} ({changed_keys})")
            else:
                _log_err(f"[registry] update failed for {self.entity_id}")
            return ok

        if existing:
            _log(f"[registry] {self._entity_type} already registered — checking for changes...", style="dim")
            diff = self._compute_update_diff(existing, desired_fields)
            if not diff:
                _log("[registry] no changes — skipping update", style="dim #8B5CF6")
                return
            _try_update(diff)
            return

        # 5. First-time registration — requires dev keypair for HD derivation proof.
        if not has_dev_key:
            _log_warn(
                f"[registry] entity not registered yet and developer keypair not found at {dev_key_path} — "
                "skipping initial registration. Run 'zynd init' or set ZYND_DEVELOPER_KEYPAIR_PATH "
                "on the box that owns this entity."
            )
            return

        try:
            dev_kp = load_keypair(str(dev_key_path))
        except Exception as e:
            _log_err(f"[registry] failed to load developer keypair: {e}")
            return

        dev_id = generate_developer_id(dev_kp.public_key_bytes)
        entity_index: int = getattr(self._config, "entity_index", None) or 0
        proof = create_derivation_proof(dev_kp, self.keypair.public_key, entity_index)

        _log(f"[registry] registering new {self._entity_type}...", style="dim")
        try:
            registered_id = dns_registry.register_entity(
                registry_url=self._config.registry_url,
                keypair=self.keypair,
                name=self._config.name,
                entity_url=entity_url,
                category=self._config.category,
                tags=self._config.tags or [],
                summary=self._config.summary or "",
                entity_type=self._entity_type,
                entity_pricing=entity_pricing,
                developer_id=dev_id,
                developer_proof=proof,
                service_endpoint=service_endpoint,
                openapi_url=openapi_url,
            )
            _log_ok(f"[registry] registered {registered_id}")
        except Exception as e:
            msg = str(e)
            # HTTP 409: GET said 404 but POST says the pubkey is already taken —
            # same keypair owns it, fall back to PUT.
            if "409" in msg:
                _log_warn(
                    "[registry] register returned 409 (entity already exists at this "
                    "public key) — falling back to update..."
                )
                _try_update(desired_fields)
                return
            _log_err(f"[registry] register failed: {msg}")

    def _start_heartbeat(self, registry_url: str):
        """Start background thread sending WebSocket heartbeats to registry."""
        debug = os.environ.get("ZYND_HEARTBEAT_DEBUG", "").lower() in ("1", "true", "yes")

        def _heartbeat_loop():
            from zyndai_agent.ed25519_identity import sign as ed25519_sign

            ws_url = registry_url.replace("https://", "wss://").replace("http://", "ws://")
            ws_url = f"{ws_url}/v1/entities/{self.entity_id}/ws"
            diag_url = ws_url.replace("wss://", "https://").replace("ws://", "http://")

            if debug:
                _log_heartbeat(f"DEBUG entity_id={self.entity_id}")
                _log_heartbeat(f"DEBUG entity_type={self._entity_type}")
                _log_heartbeat(f"DEBUG registry_url={registry_url}")
                _log_heartbeat(f"DEBUG ws_url={ws_url}")

            while not self._heartbeat_stop.is_set():
                try:
                    import websockets.sync.client as ws_client

                    _log_heartbeat(f"Connecting to {ws_url}")
                    with ws_client.connect(ws_url) as ws:
                        _log_heartbeat("Connected — sending heartbeats every 30s")
                        while not self._heartbeat_stop.is_set():
                            ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                            signature = ed25519_sign(self.keypair.private_key, ts.encode())
                            payload = json.dumps({"timestamp": ts, "signature": signature})
                            ws.send(payload)
                            _log_heartbeat(f"Sent heartbeat {ts}")

                            for _ in range(30):
                                if self._heartbeat_stop.is_set():
                                    return
                                time.sleep(1)

                except ImportError:
                    _log_warn("Heartbeat: websockets not installed. pip install websockets")
                    return
                except Exception as e:
                    self._log_heartbeat_failure(e, ws_url, diag_url)
                    for _ in range(5):
                        if self._heartbeat_stop.is_set():
                            return
                        time.sleep(1)

        self._heartbeat_thread = threading.Thread(
            target=_heartbeat_loop,
            daemon=True,
            name=f"Heartbeat-{self.entity_id}",
        )
        self._heartbeat_thread.start()

    def _log_heartbeat_failure(self, exc: Exception, ws_url: str, diag_url: str):
        """Emit a detailed diagnostic when the WS heartbeat upgrade is rejected.

        By default emits a single compact line per failure that includes
        the exception class, HTTP status (if known), and a short body
        preview (first 100 chars, stripped). This is enough to tell the
        operator what went wrong without burying their terminal in header
        dumps every 5 seconds during a reconnect loop.

        Set ZYND_HEARTBEAT_DEBUG=1 in the environment to get the full
        verbose dump instead (status code + all response headers + full
        body + a fallback plain-GET diagnostic on the same URL). Intended
        for debugging weird upgrade failures where you need to see exactly
        what the proxy / Gorilla upgrader is sending back.
        """
        exc_class = type(exc).__name__
        debug = os.environ.get("ZYND_HEARTBEAT_DEBUG", "").lower() in ("1", "true", "yes")

        # Pull status + headers + body out of whichever exception shape the
        # websockets library uses (InvalidStatus in >=13, InvalidStatusCode
        # in <13). Both may be None if the failure was e.g. a network error.
        status_code = None
        resp_headers = None
        resp_body = None

        response = getattr(exc, "response", None)
        if response is not None:
            status_code = getattr(response, "status_code", None)
            resp_headers = getattr(response, "headers", None)
            resp_body = getattr(response, "body", None)
        if status_code is None:
            status_code = getattr(exc, "status_code", None)
        if resp_headers is None:
            resp_headers = getattr(exc, "headers", None)

        # Decode body to a string once for reuse in both the terse and
        # verbose paths below.
        body_str = ""
        if resp_body:
            try:
                body_str = (
                    resp_body.decode("utf-8", errors="replace")
                    if isinstance(resp_body, (bytes, bytearray))
                    else str(resp_body)
                )
            except Exception:
                body_str = repr(resp_body)

        if not debug:
            # Terse single-line form. Examples:
            #   ♥ Connection lost [InvalidStatus HTTP 404]: {"error":"entity not found"} — reconnecting in 5s
            #   ♥ Connection lost [InvalidStatus HTTP 400]: Bad Request — reconnecting in 5s
            #   ♥ Connection lost [ConnectionError]: [Errno 111] Connection refused — reconnecting in 5s
            tag = exc_class
            if status_code is not None:
                tag = f"{exc_class} HTTP {status_code}"
            detail = body_str.strip().replace("\n", " ")[:100] if body_str else str(exc)
            _log_heartbeat(f"Connection lost [{tag}]: {detail} — reconnecting in 5s")
            return

        # Verbose form (ZYND_HEARTBEAT_DEBUG=1).
        _log_heartbeat(f"Connection lost [{exc_class}]: {exc} — reconnecting in 5s")
        if status_code is not None:
            _log_heartbeat(f"  HTTP status: {status_code}")
        if resp_headers is not None:
            try:
                items = (
                    resp_headers.raw_items() if hasattr(resp_headers, "raw_items")
                    else list(resp_headers.items())
                )
                for k, v in items:
                    _log_heartbeat(f"  < {k}: {v}")
            except Exception:
                _log_heartbeat(f"  (headers: {resp_headers!r})")
        if body_str:
            _log_heartbeat(f"  body: {body_str[:500]}")

        # Fallback plain GET on the same URL reveals Gorilla's upgrade
        # error body when the websockets lib swallows it.
        try:
            import requests as _req
            _log_heartbeat(f"  diag GET {diag_url}")
            diag = _req.get(diag_url, timeout=10, allow_redirects=False)
            _log_heartbeat(f"    status: {diag.status_code}")
            for k, v in diag.headers.items():
                _log_heartbeat(f"    < {k}: {v}")
            body_preview = (diag.text or "").strip()[:500]
            if body_preview:
                _log_heartbeat(f"    body: {body_preview}")
        except Exception as diag_err:
            _log_heartbeat(f"  diag GET failed: {diag_err}")

    def stop_heartbeat(self):
        """Stop the heartbeat background thread."""
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_stop.set()
            self._heartbeat_thread.join(timeout=5)
            _log_heartbeat("Heartbeat stopped")

    def _display_info(self):
        """Display entity info on startup."""
        name = self._config.name or "Unnamed"
        entity_id = self.entity_id
        webhook_url = getattr(self, "webhook_url", None)
        # Mirror the runtime price resolution: prefer the explicit price
        # string, fall back to formatting entity_pricing, then "Free".
        price = self._config.price
        if not price and self._config.entity_pricing:
            base = self._config.entity_pricing.get("base_price_usd")
            if isinstance(base, (int, float)) and base > 0:
                currency = self._config.entity_pricing.get("currency") or "USDC"
                price = f"${base} {currency}"
        if not price:
            price = "Free"
        pub_key = self.keypair.public_key_string if self.keypair else "-"
        address = self.pay_to_address

        fqan = None
        try:
            fqan = dns_registry.get_entity_fqan(self._config.registry_url, entity_id)
        except Exception:
            pass

        if _console:
            _console.print()
            _console.print(f"  [bold #8B5CF6]╔{'═' * 56}╗[/bold #8B5CF6]")
            _console.print(f"  [bold #8B5CF6]║[/bold #8B5CF6]  [bold white]{self._entity_label}[/bold white]{' ' * max(0, 41 - len(self._entity_label) + 13)}[bold #8B5CF6]║[/bold #8B5CF6]")
            _console.print(f"  [bold #8B5CF6]╚{'═' * 56}╝[/bold #8B5CF6]")
            _console.print()
            _console.print(f"  [dim]Name[/dim]         [bold white]{name}[/bold white]")
            if self._config.description:
                _console.print(f"  [dim]Description[/dim]  {self._config.description}")
            _console.print(f"  [dim]ID[/dim]           [#06B6D4]{entity_id}[/#06B6D4]")
            if fqan:
                _console.print(f"  [dim]FQAN[/dim]         [bold #F59E0B]{fqan}[/bold #F59E0B]")
            _console.print(f"  [dim]Public Key[/dim]   [dim]{pub_key}[/dim]")
            _console.print(f"  [dim]Address[/dim]      [dim]{address}[/dim]")
            if webhook_url:
                _console.print(f"  [dim]Webhook[/dim]      [bold #10B981]{webhook_url}[/bold #10B981]")
            ngrok_tunnel = getattr(self, "ngrok_tunnel", None)
            if ngrok_tunnel:
                _console.print(f"  [dim]Ngrok[/dim]        [bold #10B981]{ngrok_tunnel.public_url}[/bold #10B981]")
            _console.print(f"  [dim]Price[/dim]        {price}")
            _console.print()
        else:
            print(f"\n  {self._entity_label}")
            print(f"  Name        : {name}")
            print(f"  ID          : {entity_id}")
            if fqan:
                print(f"  FQAN        : {fqan}")
            if webhook_url:
                print(f"  Webhook     : {webhook_url}")
            print(f"  Price       : {price}")
            print()
