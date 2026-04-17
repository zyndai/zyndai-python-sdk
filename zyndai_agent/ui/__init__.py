"""AG-UI Protocol support for ZyndAI agents.

Provides streaming UI events over SSE (Server-Sent Events).
Import UIEmitter for handler use:

    @agent.handler
    async def invoke(msg, ui):
        await ui.text("Working...")
        await ui.tool_call("search", {"q": "python"})
"""

from zyndai_agent.ui.emitter import UIEmitter, NoOpUIEmitter
from zyndai_agent.ui.sse import SSEHandler

__all__ = [
    "UIEmitter",
    "NoOpUIEmitter",
    "SSEHandler",
]
