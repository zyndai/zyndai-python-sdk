"""ZyndAIAgent — multi-framework agent on the Zynd network (A2A protocol).

Mirrors `zyndai-ts-sdk/src/agent.ts`. Ported from the legacy
WebhookCommunicationManager-based implementation to use the new A2A
server in `zyndai_agent/a2a/`.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Callable, Optional, Type

from pydantic import BaseModel

from zyndai_agent.a2a.server import Handler, HandlerInput, TaskHandle
from zyndai_agent.base import ZyndBase, ZyndBaseConfig

logger = logging.getLogger(__name__)


class AgentFramework(str, Enum):
    LANGCHAIN = "langchain"
    LANGGRAPH = "langgraph"
    CREWAI = "crewai"
    PYDANTIC_AI = "pydantic_ai"
    CUSTOM = "custom"


class AgentConfig(ZyndBaseConfig):
    """Agent-specific config extending ZyndBaseConfig."""


class ZyndAIAgent(ZyndBase):
    """AI agent on the Zynd network.

    Two ways to wire up logic:
      1. Framework setter (set_*_agent) + invoke()  — quick path. The
         default handler converts the inbound message's text into a
         string, runs the framework, returns the result.
      2. on_message(handler) — full control. Handler receives the
         parsed HandlerInput + a TaskHandle for streaming updates,
         asking for clarification, or completing the task explicitly.
    """

    _entity_label = "ZYND AI AGENT"
    _entity_type = "agent"

    def __init__(
        self,
        config: AgentConfig,
        *,
        payload_model: Optional[Type[BaseModel]] = None,
        output_model: Optional[Type[BaseModel]] = None,
        max_body_bytes: Optional[int] = None,
    ) -> None:
        self.agent_executor: Any = None
        self.agent_framework: Optional[AgentFramework] = None
        self.custom_invoke_fn: Optional[Callable[[str], str]] = None
        self.agent_config = config
        self._user_handler: Optional[Handler] = None

        super().__init__(
            config,
            payload_model=payload_model,
            output_model=output_model,
            max_body_bytes=max_body_bytes,
        )

        # Default handler dispatches to whichever framework was wired up.
        self.install_handler(self._default_handler)

    # ---- Framework setters ----

    def set_langchain_agent(self, executor: Any) -> None:
        self.agent_executor = executor
        self.agent_framework = AgentFramework.LANGCHAIN

    def set_langgraph_agent(self, graph: Any) -> None:
        self.agent_executor = graph
        self.agent_framework = AgentFramework.LANGGRAPH

    def set_crewai_agent(self, crew: Any) -> None:
        self.agent_executor = crew
        self.agent_framework = AgentFramework.CREWAI

    def set_pydantic_ai_agent(self, agent: Any) -> None:
        self.agent_executor = agent
        self.agent_framework = AgentFramework.PYDANTIC_AI

    def set_custom_agent(self, fn: Callable[[str], str]) -> None:
        self.custom_invoke_fn = fn
        self.agent_framework = AgentFramework.CUSTOM

    # ---- Custom handler override ----

    def on_message(self, handler: Handler) -> None:
        """Override the default framework-dispatch with full control
        over the inbound message and the Task lifecycle.

        Example:
            def my_handler(input, task):
                if "translate" in input.message.content:
                    return task.complete({"translated": "..."})
                else:
                    return task.fail("don't know how")
            agent.on_message(my_handler)
        """
        self._user_handler = handler
        self.install_handler(handler)

    # ---- Universal invoke (used by default handler) ----

    def invoke(self, input_text: str, **kwargs: Any) -> str:
        if self.agent_framework == AgentFramework.LANGCHAIN:
            result = self.agent_executor.invoke({"input": input_text, **kwargs})
            return result.get("output", str(result))

        if self.agent_framework == AgentFramework.LANGGRAPH:
            result = self.agent_executor.invoke(
                {"messages": [("user", input_text)], **kwargs}
            )
            messages = result.get("messages") if isinstance(result, dict) else None
            if messages:
                last = messages[-1]
                return getattr(last, "content", str(last))
            return str(result)

        if self.agent_framework == AgentFramework.CREWAI:
            result = self.agent_executor.kickoff(inputs={"query": input_text, **kwargs})
            return getattr(result, "raw", str(result))

        if self.agent_framework == AgentFramework.PYDANTIC_AI:
            result = self.agent_executor.run_sync(input_text, **kwargs)
            return str(getattr(result, "data", result))

        if self.agent_framework == AgentFramework.CUSTOM:
            if self.custom_invoke_fn is None:
                raise ValueError("Custom agent invoke function not set")
            return self.custom_invoke_fn(input_text)

        raise ValueError(f"Unknown agent framework: {self.agent_framework}")

    # ---- Default handler ----

    def _default_handler(self, input: HandlerInput, task: TaskHandle) -> Any:
        if self._user_handler is not None:
            return self._user_handler(input, task)
        if self.agent_framework is None:
            return task.fail(
                "Agent has no framework registered. Call set_*_agent() or on_message() first."
            )
        try:
            text = self.invoke(input.message.content)
            return {"text": text}
        except Exception as e:
            return task.fail(str(e))
