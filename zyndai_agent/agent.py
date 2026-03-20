import asyncio
import base64
import hashlib
import json
import logging
import os
import threading
import time
import requests
from zyndai_agent.search import SearchAndDiscoveryManager
from zyndai_agent.identity import IdentityManager
from zyndai_agent.communication import AgentCommunicationManager
from zyndai_agent.webhook_communication import WebhookCommunicationManager
from zyndai_agent.payment import X402PaymentProcessor
from zyndai_agent.config_manager import ConfigManager
from zyndai_agent.ed25519_identity import (
    Ed25519Keypair,
    keypair_from_private_bytes,
    create_derivation_proof,
)
from zyndai_agent.agent_card import build_agent_card, sign_agent_card
from zyndai_agent.agent_card_loader import (
    load_agent_card,
    resolve_keypair,
    build_runtime_card,
    compute_card_hash,
    resolve_card_from_config,
    load_derivation_metadata,
)
from zyndai_agent import dns_registry
from pydantic import BaseModel
from typing import Optional, Any, Callable, Union, List
from enum import Enum

logger = logging.getLogger(__name__)


class AgentFramework(str, Enum):
    """Supported AI agent frameworks."""

    LANGCHAIN = "langchain"
    LANGGRAPH = "langgraph"
    CREWAI = "crewai"
    PYDANTIC_AI = "pydantic_ai"
    CUSTOM = "custom"


class AgentConfig(BaseModel):
    name: str = ""
    description: str = ""
    capabilities: Optional[dict] = None

    auto_reconnect: bool = True
    message_history_limit: int = 100
    registry_url: str = "http://localhost:8080"

    # Webhook configuration
    webhook_host: Optional[str] = "0.0.0.0"
    webhook_port: Optional[int] = 5000
    webhook_url: Optional[str] = None  # Public URL if behind NAT

    # agent-dns fields
    category: str = "general"
    tags: Optional[List[str]] = None
    summary: Optional[str] = None  # Max 200 chars for registry record
    developer_keypair_path: Optional[str] = None  # For HD derivation
    agent_index: Optional[int] = None  # Derivation index

    # Ngrok configuration
    use_ngrok: bool = False
    ngrok_auth_token: Optional[str] = None

    # MQTT configuration (deprecated, kept for backward compatibility)
    mqtt_broker_url: Optional[str] = None
    default_outbox_topic: Optional[str] = None

    price: Optional[str] = None

    # Config directory for agent identity (allows multiple agents in same project)
    config_dir: Optional[str] = None

    # Keypair and registration
    keypair_path: Optional[str] = None     # Path to keypair JSON
    auto_register: bool = True             # Self-register on startup
    card_output: Optional[str] = None      # Output path for .well-known/agent.json (default: .well-known/agent.json)


class ZyndAIAgent(
    SearchAndDiscoveryManager,
    IdentityManager,
    X402PaymentProcessor,
    WebhookCommunicationManager,
):
    def __init__(self, agent_config: AgentConfig):
        self.agent_executor: Any = None
        self.agent_framework: AgentFramework = None
        self.custom_invoke_fn: Callable = None
        self.agent_config = agent_config
        self.communication_mode = None  # Track which mode is active
        self._static_card = None  # Card dict built from AgentConfig (for self-registration)

        # Try to resolve keypair from env/path first (card init flow)
        env_keypair = self._try_resolve_keypair(agent_config)

        if env_keypair:
            # === Keypair from env/path — skip ConfigManager ===
            self.keypair = env_keypair
            self.agent_id = self.keypair.agent_id

            # x402 payment
            self.x402_processor = X402PaymentProcessor(
                ed25519_private_key_bytes=self.keypair.private_key_bytes
            )
            self.pay_to_address = self.x402_processor.account.address

            IdentityManager.__init__(self, agent_config.registry_url)
            SearchAndDiscoveryManager.__init__(self, registry_url=agent_config.registry_url)

        else:
            # === Fallback: load keypair from .agent/config.json (legacy) ===
            config = ConfigManager.load_or_create(agent_config)

            self.agent_id = config.get("agent_id", config.get("id"))

            private_key_b64 = config.get("private_key")
            if private_key_b64:
                private_bytes = base64.b64decode(private_key_b64)
                self.keypair = keypair_from_private_bytes(private_bytes)
            else:
                self.keypair = None

            # x402 payment: use legacy_seed if available, otherwise Ed25519 key
            legacy_seed = config.get("legacy_seed")
            if legacy_seed:
                self.x402_processor = X402PaymentProcessor(agent_seed=legacy_seed)
            elif self.keypair:
                self.x402_processor = X402PaymentProcessor(
                    ed25519_private_key_bytes=self.keypair.private_key_bytes
                )
            else:
                seed = config.get("seed")
                if seed:
                    self.x402_processor = X402PaymentProcessor(agent_seed=seed)
                else:
                    raise ValueError("No key material available for x402 payment processor")

            self.pay_to_address = self.x402_processor.account.address

            IdentityManager.__init__(self, agent_config.registry_url)
            SearchAndDiscoveryManager.__init__(self, registry_url=agent_config.registry_url)

        # Build card dict from AgentConfig (used for serving and self-registration)
        self._static_card = resolve_card_from_config(agent_config)

        # Agent card builder: builds runtime card from AgentConfig on every request
        def _build_agent_card():
            if not self.keypair:
                return {}
            base_url = self._get_base_url()
            return build_runtime_card(self._static_card, base_url, self.keypair)

        # Determine communication mode: webhook or MQTT
        if (
            agent_config.webhook_port is not None
            and agent_config.mqtt_broker_url is None
        ):
            self.communication_mode = "webhook"
            WebhookCommunicationManager.__init__(
                self,
                agent_id=self.agent_id,
                webhook_host=agent_config.webhook_host,
                webhook_port=agent_config.webhook_port,
                webhook_url=agent_config.webhook_url,
                auto_restart=agent_config.auto_reconnect,
                message_history_limit=agent_config.message_history_limit,
                identity_credential=None,
                keypair=self.keypair,
                agent_card_builder=_build_agent_card,
                price=agent_config.price,
                pay_to_address=self.pay_to_address,
                use_ngrok=agent_config.use_ngrok,
                ngrok_auth_token=agent_config.ngrok_auth_token or os.environ.get("NGROK_AUTH_TOKEN"),
            )

        elif agent_config.mqtt_broker_url is not None:
            self.communication_mode = "mqtt"
            legacy_config = config if not env_keypair else {}
            identity_credential = legacy_config.get("did", {})
            AgentCommunicationManager.__init__(
                self,
                self.agent_id,
                default_inbox_topic=f"{self.agent_id}/inbox",
                default_outbox_topic=agent_config.default_outbox_topic,
                auto_reconnect=True,
                message_history_limit=agent_config.message_history_limit,
                identity_credential=identity_credential,
                secret_seed=legacy_config.get("seed", ""),
                mqtt_broker_url=agent_config.mqtt_broker_url,
            )
        else:
            raise ValueError(
                "Either webhook_port or mqtt_broker_url must be configured"
            )

        # Write .well-known/agent.json to disk on every startup
        if self.keypair and self.communication_mode == "webhook":
            self._write_card_file()

        # Self-register on the registry
        if agent_config.auto_register and self.keypair:
            self._self_register(agent_config.registry_url)

        # Start heartbeat background thread
        self._heartbeat_thread = None
        self._heartbeat_stop = threading.Event()
        if self.keypair and agent_config.auto_register:
            self._start_heartbeat(agent_config.registry_url)

        # Display agent info
        self._display_agent_info()

    def set_agent_executor(
        self, agent_executor: Any, framework: AgentFramework = AgentFramework.LANGCHAIN
    ):
        """
        Set the agent executor for the agent.

        Args:
            agent_executor: The agent executor instance (LangChain AgentExecutor, LangGraph, CrewAI Crew, etc.)
            framework: The framework type (langchain, langgraph, crewai, pydantic_ai, custom)
        """
        self.agent_executor = agent_executor
        self.agent_framework = framework

    def set_langchain_agent(self, agent_executor):
        """Set a LangChain AgentExecutor."""
        self.agent_executor = agent_executor
        self.agent_framework = AgentFramework.LANGCHAIN

    def set_langgraph_agent(self, graph):
        """Set a LangGraph compiled graph."""
        self.agent_executor = graph
        self.agent_framework = AgentFramework.LANGGRAPH

    def set_crewai_agent(self, crew):
        """Set a CrewAI Crew instance."""
        self.agent_executor = crew
        self.agent_framework = AgentFramework.CREWAI

    def set_pydantic_ai_agent(self, agent):
        """Set a PydanticAI Agent instance."""
        self.agent_executor = agent
        self.agent_framework = AgentFramework.PYDANTIC_AI

    def set_custom_agent(self, invoke_fn: Callable[[str], str]):
        """
        Set a custom agent with a simple invoke function.

        Args:
            invoke_fn: A function that takes a string input and returns a string output
        """
        self.custom_invoke_fn = invoke_fn
        self.agent_framework = AgentFramework.CUSTOM

    def invoke(self, input_text: str, **kwargs) -> str:
        """
        Invoke the agent with the given input, regardless of framework.

        Args:
            input_text: The input text/query for the agent
            **kwargs: Additional arguments passed to the underlying framework

        Returns:
            The agent's response as a string
        """
        if self.agent_framework == AgentFramework.LANGCHAIN:
            result = self.agent_executor.invoke({"input": input_text, **kwargs})
            return result.get("output", str(result))

        elif self.agent_framework == AgentFramework.LANGGRAPH:
            result = self.agent_executor.invoke(
                {"messages": [("user", input_text)], **kwargs}
            )
            # Extract the last message content
            if "messages" in result and len(result["messages"]) > 0:
                last_message = result["messages"][-1]
                if hasattr(last_message, "content"):
                    return last_message.content
                return str(last_message)
            return str(result)

        elif self.agent_framework == AgentFramework.CREWAI:
            result = self.agent_executor.kickoff(inputs={"query": input_text, **kwargs})
            # CrewAI returns a CrewOutput object
            if hasattr(result, "raw"):
                return result.raw
            return str(result)

        elif self.agent_framework == AgentFramework.PYDANTIC_AI:
            # PydanticAI uses run_sync for synchronous execution
            result = self.agent_executor.run_sync(input_text, **kwargs)
            if hasattr(result, "data"):
                return str(result.data)
            return str(result)

        elif self.agent_framework == AgentFramework.CUSTOM:
            if self.custom_invoke_fn:
                return self.custom_invoke_fn(input_text)
            raise ValueError("Custom agent invoke function not set")

        else:
            raise ValueError(f"Unknown agent framework: {self.agent_framework}")

    def _start_heartbeat(self, registry_url: str):
        """Start a background thread that sends WebSocket heartbeats to the registry."""

        def _heartbeat_loop():
            from zyndai_agent.ed25519_identity import sign as ed25519_sign

            # Convert http(s) URL to ws(s) URL
            ws_url = registry_url.replace("https://", "wss://").replace("http://", "ws://")
            ws_url = f"{ws_url}/v1/agents/{self.agent_id}/ws"

            while not self._heartbeat_stop.is_set():
                try:
                    import websockets.sync.client as ws_client

                    logger.info(f"Heartbeat: connecting to {ws_url}")
                    with ws_client.connect(ws_url) as ws:
                        logger.info(f"Heartbeat: connected")
                        while not self._heartbeat_stop.is_set():
                            ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                            signature = ed25519_sign(self.keypair.private_key, ts.encode())
                            payload = json.dumps({"timestamp": ts, "signature": signature})
                            ws.send(payload)
                            logger.info(f"Heartbeat sent: {ts}")

                            # Sleep 30s in small increments so we can stop quickly
                            for _ in range(30):
                                if self._heartbeat_stop.is_set():
                                    return
                                time.sleep(1)

                except ImportError:
                    logger.warning(
                        "Heartbeat: websockets package not installed. "
                        "Install with: pip install websockets"
                    )
                    return
                except Exception as e:
                    logger.debug(f"Heartbeat connection failed: {e}")
                    # Reconnect after 5s
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
        logger.info("Heartbeat thread started")

    def stop_heartbeat(self):
        """Stop the heartbeat background thread."""
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_stop.set()
            self._heartbeat_thread.join(timeout=5)
            logger.info("Heartbeat thread stopped")

    @staticmethod
    def _try_resolve_keypair(agent_config) -> Optional[Ed25519Keypair]:
        """Try to resolve keypair from env vars or agent_config.keypair_path.
        Returns None if no external keypair is available (falls back to legacy ConfigManager).
        """
        try:
            return resolve_keypair(agent_config)
        except ValueError:
            return None

    def _write_card_file(self):
        """Write .well-known/agent.json to disk from AgentConfig fields."""
        card_path = self.agent_config.card_output or os.path.join(".well-known", "agent.json")
        card_dir = os.path.dirname(card_path)
        if card_dir:
            os.makedirs(card_dir, exist_ok=True)

        base_url = self._get_base_url()
        runtime_card = build_runtime_card(self._static_card, base_url, self.keypair)

        with open(card_path, "w") as f:
            json.dump(runtime_card, f, indent=2)

        logger.info(f"Agent card written to {card_path}")

    def _get_base_url(self) -> str:
        """Get the agent's base URL from the webhook URL."""
        webhook_url = getattr(self, "webhook_url", None) or ""
        if webhook_url.endswith("/webhook"):
            return webhook_url[: -len("/webhook")]
        return webhook_url

    def _self_register(self, registry_url: str):
        """
        Self-register or update on the registry based on card content.

        Compares the current card hash with the stored hash to detect changes.
        If the agent is not registered or the card has changed, registers/updates.
        """
        if not self.keypair or not self._static_card:
            return

        base_url = self._get_base_url()
        runtime_card = build_runtime_card(self._static_card, base_url, self.keypair)
        current_hash = compute_card_hash(self._static_card)
        stored_hash = self._load_card_hash()

        try:
            existing = dns_registry.get_agent(registry_url, self.agent_id)

            if existing is None:
                # Not registered — register with developer proof if available
                developer_proof = None
                developer_id = None

                # Try to get derivation metadata for developer proof
                keypair_path = os.environ.get("ZYND_AGENT_KEYPAIR_PATH") or \
                               getattr(self.agent_config, "keypair_path", None)
                if keypair_path:
                    derivation = load_derivation_metadata(keypair_path)
                    if derivation:
                        # We have derivation metadata but need the developer's private key
                        # to create a proof. Store developer_id for registration.
                        developer_id = None  # Would need dev keypair to derive this

                dns_registry.register_agent(
                    registry_url=registry_url,
                    keypair=self.keypair,
                    name=self._static_card.get("name", ""),
                    agent_url=base_url,
                    category=self._static_card.get("category", "general"),
                    tags=self._static_card.get("tags"),
                    summary=self._static_card.get("summary"),
                    developer_id=developer_id,
                    developer_proof=developer_proof,
                )
                print(f"Agent registered on registry: {self.agent_id}")

            elif current_hash != stored_hash:
                # Card changed — update registry
                updates = {
                    "name": self._static_card.get("name", ""),
                    "agent_url": base_url,
                    "category": self._static_card.get("category", "general"),
                    "tags": self._static_card.get("tags", []),
                    "summary": self._static_card.get("summary", ""),
                }
                if dns_registry.update_agent(registry_url, self.agent_id, self.keypair, updates):
                    print(f"Agent updated on registry: {self.agent_id}")
                else:
                    print(f"Warning: Failed to update agent on registry")
            else:
                print(f"Agent already registered (no changes): {self.agent_id}")

            self._save_card_hash(current_hash)

        except Exception as e:
            logger.warning(f"Self-registration failed: {e}")
            print(f"Warning: Could not self-register on registry: {e}")

    def _load_card_hash(self) -> Optional[str]:
        """Load the stored card hash from .agent/card_hash."""
        config_dir = getattr(self.agent_config, "config_dir", None) or ".agent"
        hash_path = os.path.join(os.getcwd(), config_dir, "card_hash")
        if os.path.exists(hash_path):
            with open(hash_path, "r") as f:
                return f.read().strip()
        return None

    def _save_card_hash(self, card_hash: str):
        """Save the card hash to .agent/card_hash."""
        config_dir = getattr(self.agent_config, "config_dir", None) or ".agent"
        dir_path = os.path.join(os.getcwd(), config_dir)
        os.makedirs(dir_path, exist_ok=True)
        hash_path = os.path.join(dir_path, "card_hash")
        with open(hash_path, "w") as f:
            f.write(card_hash)

    def _display_agent_info(self):
        """Display agent information in a pretty format on startup."""
        name = self.agent_config.name or "Unnamed Agent"
        description = self.agent_config.description or "-"
        agent_id = self.agent_id
        address = self.pay_to_address
        mode = self.communication_mode or "-"
        webhook_url = getattr(self, "webhook_url", None)
        price = self.agent_config.price or "Free"

        # Show public key if available
        pub_key = self.keypair.public_key_string if self.keypair else "-"

        border = "=" * 60
        print(f"\n{border}")
        print(f"  ZYND AI AGENT")
        print(f"{border}")
        print(f"  Name        : {name}")
        print(f"  Description : {description}")
        print(f"  Agent ID    : {agent_id}")
        print(f"  Public Key  : {pub_key}")
        print(f"  Address     : {address}")
        print(f"  Mode        : {mode}")
        if webhook_url:
            print(f"  Webhook URL : {webhook_url}")
        ngrok_tunnel = getattr(self, "ngrok_tunnel", None)
        if ngrok_tunnel:
            print(f"  Ngrok       : Active ({ngrok_tunnel.public_url})")
        elif self.agent_config.use_ngrok:
            print(f"  Ngrok       : Configured (not connected)")
        print(f"  Price       : {price}")
        print(f"{border}\n")
