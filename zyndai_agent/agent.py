import base64
import json
import logging
import os
import threading
from zyndai_agent.search import SearchAndDiscoveryManager
from zyndai_agent.identity import IdentityManager
from zyndai_agent.communication import AgentCommunicationManager
from zyndai_agent.webhook_communication import WebhookCommunicationManager
from zyndai_agent.payment import X402PaymentProcessor
from zyndai_agent.config_manager import ConfigManager
from zyndai_agent.ed25519_identity import (
    Ed25519Keypair,
    keypair_from_private_bytes,
)
from zyndai_agent.entity_card_loader import (
    resolve_keypair,
)
from zyndai_agent.base import ZyndBase, ZyndBaseConfig, _console, _log, _log_ok, _log_warn, _log_err, _log_heartbeat
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


class AgentConfig(ZyndBaseConfig):
    """Agent-specific config extending ZyndBaseConfig."""

    # Agent-specific fields
    developer_keypair_path: Optional[str] = None
    entity_index: Optional[int] = None

    # MQTT (legacy, kept for backward compatibility)
    mqtt_broker_url: Optional[str] = None
    default_outbox_topic: Optional[str] = None


class ZyndAIAgent(ZyndBase):
    """
    AI Agent on the Zynd network.

    Supports LangChain, LangGraph, CrewAI, PydanticAI, and custom frameworks.
    Inherits identity, webhook, heartbeat, and payment from ZyndBase.

    For MQTT communication (legacy), falls back to direct initialization
    bypassing the base class webhook setup.
    """

    _entity_label = "ZYND AI AGENT"
    _entity_type = "agent"

    def __init__(self, agent_config: AgentConfig):
        self.agent_executor: Any = None
        self.agent_framework: AgentFramework = None
        self.custom_invoke_fn: Callable = None
        self.agent_config = agent_config
        self.communication_mode = None

        # MQTT path: legacy, bypasses ZyndBase (which only does webhooks)
        if agent_config.mqtt_broker_url is not None:
            self._init_mqtt(agent_config)
            return

        # Webhook path: use ZyndBase for all shared infra
        self.communication_mode = "webhook"
        super().__init__(agent_config)

    def _init_mqtt(self, agent_config: AgentConfig):
        """Legacy MQTT initialization — bypasses ZyndBase."""
        self.communication_mode = "mqtt"

        env_keypair = self._try_resolve_keypair_legacy(agent_config)
        if env_keypair:
            self.keypair = env_keypair
            self.entity_id = self.keypair.entity_id
            self.x402_processor = X402PaymentProcessor(
                ed25519_private_key_bytes=self.keypair.private_key_bytes
            )
            self.pay_to_address = self.x402_processor.account.address
            IdentityManager.__init__(self, agent_config.registry_url)
            SearchAndDiscoveryManager.__init__(self, registry_url=agent_config.registry_url)
            config = {}
        else:
            config = ConfigManager.load_or_create(agent_config)
            self.entity_id = config.get("entity_id", config.get("id"))
            private_key_b64 = config.get("private_key")
            if private_key_b64:
                private_bytes = base64.b64decode(private_key_b64)
                self.keypair = keypair_from_private_bytes(private_bytes)
            else:
                self.keypair = None

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

        identity_credential = config.get("did", {}) if config else {}
        AgentCommunicationManager.__init__(
            self,
            self.entity_id,
            default_inbox_topic=f"{self.entity_id}/inbox",
            default_outbox_topic=agent_config.default_outbox_topic,
            auto_reconnect=True,
            message_history_limit=agent_config.message_history_limit,
            identity_credential=identity_credential,
            secret_seed=config.get("seed", "") if config else "",
            mqtt_broker_url=agent_config.mqtt_broker_url,
        )

        self._heartbeat_thread = None
        self._heartbeat_stop = threading.Event()
        if self.keypair:
            self._start_heartbeat(agent_config.registry_url)
        self._display_info()

    @staticmethod
    def _try_resolve_keypair_legacy(agent_config) -> Optional[Ed25519Keypair]:
        try:
            return resolve_keypair(agent_config)
        except ValueError:
            return None

    # ---- Framework setters ----

    def set_agent_executor(
        self, agent_executor: Any, framework: AgentFramework = AgentFramework.LANGCHAIN
    ):
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
        """Set a custom agent with a simple invoke function."""
        self.custom_invoke_fn = invoke_fn
        self.agent_framework = AgentFramework.CUSTOM

    # ---- Universal invoke ----

    def invoke(self, input_text: str, **kwargs) -> str:
        """Invoke the agent, regardless of framework."""
        if self.agent_framework == AgentFramework.LANGCHAIN:
            result = self.agent_executor.invoke({"input": input_text, **kwargs})
            return result.get("output", str(result))

        elif self.agent_framework == AgentFramework.LANGGRAPH:
            result = self.agent_executor.invoke(
                {"messages": [("user", input_text)], **kwargs}
            )
            if "messages" in result and len(result["messages"]) > 0:
                last_message = result["messages"][-1]
                if hasattr(last_message, "content"):
                    return last_message.content
                return str(last_message)
            return str(result)

        elif self.agent_framework == AgentFramework.CREWAI:
            result = self.agent_executor.kickoff(inputs={"query": input_text, **kwargs})
            if hasattr(result, "raw"):
                return result.raw
            return str(result)

        elif self.agent_framework == AgentFramework.PYDANTIC_AI:
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

    # ---- Legacy helpers (kept for backward compat) ----

    def _load_card_hash(self) -> Optional[str]:
        config_dir = getattr(self.agent_config, "config_dir", None) or ".agent"
        hash_path = os.path.join(os.getcwd(), config_dir, "card_hash")
        if os.path.exists(hash_path):
            with open(hash_path, "r") as f:
                return f.read().strip()
        return None

    def _save_card_hash(self, card_hash: str):
        config_dir = getattr(self.agent_config, "config_dir", None) or ".agent"
        dir_path = os.path.join(os.getcwd(), config_dir)
        os.makedirs(dir_path, exist_ok=True)
        hash_path = os.path.join(dir_path, "card_hash")
        with open(hash_path, "w") as f:
            f.write(card_hash)
