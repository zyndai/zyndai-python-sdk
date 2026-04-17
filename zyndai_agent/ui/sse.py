"""Flask SSE handler for AG-UI event streaming."""

import asyncio
import logging
from typing import Optional
from flask import Response

logger = logging.getLogger(__name__)


class SSEHandler:
    """
    Server-Sent Events handler for AG-UI events.

    Drains UIEmitter.queue and yields SSE-formatted events.
    """

    @staticmethod
    async def stream_events(
        queue: asyncio.Queue,
        timeout_seconds: float = 300,
    ):
        """
        Async generator: drain queue and yield SSE events.

        Args:
            queue: asyncio.Queue from UIEmitter
            timeout_seconds: max time to wait for next event before closing

        Yields:
            SSE-formatted event lines
        """
        start_time = asyncio.get_event_loop().time()

        while True:
            try:
                # Wait for event with timeout
                event = await asyncio.wait_for(
                    queue.get(),
                    timeout=timeout_seconds,
                )

                # Yield SSE-formatted event
                json_line = event.to_json()
                yield f"data: {json_line}\n\n"

            except asyncio.TimeoutError:
                logger.debug("SSE stream timeout, closing")
                break
            except Exception as e:
                logger.error(f"SSE stream error: {e}")
                break

    @staticmethod
    def create_response(queue: Optional[asyncio.Queue]) -> Response:
        """
        Create Flask SSE Response from UIEmitter queue.

        Args:
            queue: asyncio.Queue from UIEmitter (or None if no-op)

        Returns:
            Flask Response with SSE headers
        """
        if queue is None:
            # No-op emitter
            def noop_generator():
                yield "data: {}\n\n"

            return Response(
                noop_generator(),
                mimetype="text/event-stream",
            )

        async def event_generator():
            """Drains queue in async context."""
            async for event_line in SSEHandler.stream_events(queue):
                yield event_line

        # Convert async generator to sync (Flask compatibility)
        def sync_generator():
            """Wrapper to run async generator in sync context."""
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                async_gen = event_generator()
                while True:
                    try:
                        yield loop.run_until_complete(
                            async_gen.__anext__()
                        )
                    except StopAsyncIteration:
                        break
            finally:
                loop.close()

        return Response(
            sync_generator(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )
