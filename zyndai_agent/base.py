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
from zyndai_agent.agent_card_loader import (
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

    # Webhook
    webhook_host: Optional[str] = "0.0.0.0"
    webhook_port: Optional[int] = 5000
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

    def __init__(self, config: ZyndBaseConfig):
        self._config = config
        self._static_card = None

        # Resolve keypair
        self.keypair = self._resolve_keypair(config)
        if not self.keypair:
            raise ValueError(
                "Keypair not found. Set ZYND_AGENT_KEYPAIR_PATH / ZYND_SERVICE_KEYPAIR_PATH "
                "or provide keypair_path in config."
            )

        self.agent_id = self.keypair.agent_id

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
            card = build_runtime_card(self._static_card, base_url, self.keypair)
            if self._entity_type == "service":
                card["type"] = "service"
            return card

        self._build_card = _build_card

        # Start webhook server
        WebhookCommunicationManager.__init__(
            self,
            agent_id=self.agent_id,
            webhook_host=config.webhook_host,
            webhook_port=config.webhook_port,
            webhook_url=config.webhook_url,
            auto_restart=config.auto_reconnect,
            message_history_limit=config.message_history_limit,
            identity_credential=None,
            keypair=self.keypair,
            agent_card_builder=_build_card,
            price=config.price,
            pay_to_address=self.pay_to_address,
            use_ngrok=config.use_ngrok,
            ngrok_auth_token=config.ngrok_auth_token or os.environ.get("NGROK_AUTH_TOKEN"),
        )

        # Write card to disk
        self._write_card_file()

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
        webhook_url = getattr(self, "webhook_url", None) or ""
        if webhook_url.endswith("/webhook"):
            return webhook_url[:-8]
        return webhook_url

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

    def _start_heartbeat(self, registry_url: str):
        """Start background thread sending WebSocket heartbeats to registry."""
        def _heartbeat_loop():
            from zyndai_agent.ed25519_identity import sign as ed25519_sign

            ws_url = registry_url.replace("https://", "wss://").replace("http://", "ws://")
            ws_url = f"{ws_url}/v1/agents/{self.agent_id}/ws"

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
                    _log_heartbeat(f"Connection lost: {e} — reconnecting in 5s")
                    for _ in range(5):
                        if self._heartbeat_stop.is_set():
                            return
                        time.sleep(1)

        self._heartbeat_thread = threading.Thread(
            target=_heartbeat_loop,
            daemon=True,
            name=f"Heartbeat-{self.agent_id}",
        )
        self._heartbeat_thread.start()

    def stop_heartbeat(self):
        """Stop the heartbeat background thread."""
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_stop.set()
            self._heartbeat_thread.join(timeout=5)
            _log_heartbeat("Heartbeat stopped")

    def _display_info(self):
        """Display entity info on startup."""
        name = self._config.name or "Unnamed"
        agent_id = self.agent_id
        webhook_url = getattr(self, "webhook_url", None)
        price = self._config.price or "Free"
        pub_key = self.keypair.public_key_string if self.keypair else "-"
        address = self.pay_to_address

        fqan = None
        try:
            fqan = dns_registry.get_agent_fqan(self._config.registry_url, agent_id)
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
            _console.print(f"  [dim]ID[/dim]           [#06B6D4]{agent_id}[/#06B6D4]")
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
            print(f"  ID          : {agent_id}")
            if fqan:
                print(f"  FQAN        : {fqan}")
            if webhook_url:
                print(f"  Webhook     : {webhook_url}")
            print(f"  Price       : {price}")
            print()
