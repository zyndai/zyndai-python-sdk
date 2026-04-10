"""
ZyndService — A service wrapper for the Zynd network.

Unlike ZyndAIAgent which wraps LLM frameworks (LangChain, CrewAI, etc.),
ZyndService wraps plain Python functions. Same identity, webhook, heartbeat,
and payment infrastructure — but instead of executing an agent chain,
it just runs the function passed to it.
"""

import logging
from typing import Optional, Callable, List

from pydantic import BaseModel
from zyndai_agent.base import ZyndBase, ZyndBaseConfig
from zyndai_agent.message import AgentMessage

logger = logging.getLogger(__name__)


class ServiceConfig(ZyndBaseConfig):
    """Configuration for a Zynd service. Extends ZyndBaseConfig with service-specific fields."""
    service_endpoint: Optional[str] = None
    openapi_url: Optional[str] = None


class ZyndService(ZyndBase):
    """
    A service on the Zynd network.

    Wraps a plain Python function with identity, webhook, heartbeat,
    and payment infrastructure. Instead of invoking an LLM chain,
    it calls the handler function directly.

    Usage:
        service = ZyndService(ServiceConfig(name="My Service", ...))
        service.set_handler(my_function)
    """

    _entity_label = "ZYND SERVICE"
    _entity_type = "service"

    def __init__(self, service_config: ServiceConfig):
        self.service_config = service_config
        self._handler_fn: Optional[Callable] = None
        super().__init__(service_config)

    def set_handler(self, fn: Callable[[str], str]):
        """
        Set the service handler function.

        The function receives the request content as a string and returns
        the response as a string. Auto-wires the webhook message handler.

        Args:
            fn: A callable that takes a string input and returns a string output.
        """
        self._handler_fn = fn

        def _message_handler(message: AgentMessage, topic):
            try:
                result = fn(message.content)
                self.set_response(message.message_id, result)
            except Exception as e:
                logger.error(f"Service handler error: {e}")
                self.set_response(message.message_id, f"Error: {str(e)}")

        self.add_message_handler(_message_handler)

    def invoke(self, input_text: str, **kwargs) -> str:
        """Invoke the service handler directly."""
        if not self._handler_fn:
            raise ValueError("No handler function set. Call set_handler() first.")
        return self._handler_fn(input_text)
