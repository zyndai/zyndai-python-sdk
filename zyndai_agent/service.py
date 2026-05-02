"""ZyndService — stateless service entity on the Zynd network (A2A).

Mirrors `zyndai-ts-sdk/src/service.ts`. Two surfaces:
  - set_handler(fn): str -> str  — simple in/out
  - on_message(handler)          — full A2A access (parts, attachments, tasks)
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Awaitable, Callable, Optional, Type, Union

from pydantic import BaseModel

from zyndai_agent.a2a.server import Handler, HandlerInput, TaskHandle
from zyndai_agent.base import ZyndBase, ZyndBaseConfig


class ServiceConfig(ZyndBaseConfig):
    service_endpoint: Optional[str] = None
    openapi_url: Optional[str] = None


SimpleHandlerFn = Callable[[str], Union[str, Awaitable[str]]]


class ZyndService(ZyndBase):
    _entity_label = "ZYND SERVICE"
    _entity_type = "service"

    def __init__(
        self,
        config: ServiceConfig,
        *,
        payload_model: Optional[Type[BaseModel]] = None,
        output_model: Optional[Type[BaseModel]] = None,
        max_body_bytes: Optional[int] = None,
    ) -> None:
        self._handler_fn: Optional[SimpleHandlerFn] = None
        super().__init__(
            config,
            payload_model=payload_model,
            output_model=output_model,
            max_body_bytes=max_body_bytes,
        )
        self.install_handler(self._default_handler)

    def set_handler(self, fn: SimpleHandlerFn) -> None:
        """Simple string-in / string-out handler."""
        self._handler_fn = fn
        self.install_handler(self._default_handler)

    def on_message(self, handler: Handler) -> None:
        """Full A2A handler — overrides the simple form."""
        self.install_handler(handler)

    def invoke(self, input_text: str) -> str:
        if self._handler_fn is None:
            raise ValueError("No handler function set. Call set_handler() first.")
        return self._call_simple(input_text)

    # -------------------------------------------------------------------------
    # Default handler
    # -------------------------------------------------------------------------

    def _default_handler(self, input: HandlerInput, task: TaskHandle) -> Any:
        if self._handler_fn is None:
            return task.fail("ZyndService has no handler. Call set_handler() first.")
        try:
            result = self._call_simple(input.message.content)
            return {"text": result}
        except Exception as e:
            return task.fail(str(e))

    def _call_simple(self, text: str) -> str:
        result = self._handler_fn(text)  # type: ignore[misc]
        if inspect.isawaitable(result):
            return asyncio.run(result)  # type: ignore[arg-type]
        return result  # type: ignore[return-value]
