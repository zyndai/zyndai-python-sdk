"""UIEmitter: per-conversation AG-UI event streaming.

Manages per-conversation asyncio.Queue for AG-UI events.
Backpressure: drops events if no subscriber (doesn't block invoke).
"""

import asyncio
import time
import logging
from typing import Optional, Dict, Any, List
from zyndai_agent.ui.events import (
    AGUIEvent,
    RunStartedEvent,
    RunFinishedEvent,
    RunErrorEvent,
    TextMessageContentEvent,
    ToolCallStartEvent,
    ToolCallEndEvent,
    StateDeltaEvent,
    StateSnapshotEvent,
    CustomEvent,
)

logger = logging.getLogger(__name__)


class UIEmitter:
    """
    Per-conversation AG-UI event emitter.

    Usage:
        ui = UIEmitter(conversation_id="conv-123")
        await ui.text("Working...")
        await ui.tool_call("search", {"q": "python"})
    """

    MAX_QUEUE_SIZE = 1000
    STATS_LOG_INTERVAL = 100  # Log stats every N events

    def __init__(self, conversation_id: str):
        self.conversation_id = conversation_id
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=self.MAX_QUEUE_SIZE)
        self.events_emitted = 0
        self.events_dropped = 0
        self.run_id = f"run-{int(time.time())}"
        self._start_time = time.time()

    async def _emit(self, event: AGUIEvent) -> bool:
        """
        Emit event to queue. Backpressure: drops if full (doesn't block).

        Returns True if emitted, False if dropped.
        """
        try:
            self.queue.put_nowait(event)
            self.events_emitted += 1

            if self.events_emitted % self.STATS_LOG_INTERVAL == 0:
                logger.debug(
                    f"[{self.conversation_id}] emitted {self.events_emitted} events, "
                    f"dropped {self.events_dropped}"
                )

            return True
        except asyncio.QueueFull:
            self.events_dropped += 1
            logger.warning(
                f"[{self.conversation_id}] event queue full, dropping (dropped={self.events_dropped})"
            )
            return False

    async def run_started(self):
        """Emit RUN_STARTED."""
        await self._emit(
            RunStartedEvent(runId=self.run_id, timestamp=time.time())
        )

    async def run_finished(self):
        """Emit RUN_FINISHED."""
        elapsed_ms = int((time.time() - self._start_time) * 1000)
        await self._emit(
            RunFinishedEvent(
                runId=self.run_id,
                timestamp=time.time(),
                elapsedMs=elapsed_ms,
            )
        )

    async def run_error(self, error: str):
        """Emit RUN_ERROR."""
        await self._emit(
            RunErrorEvent(
                runId=self.run_id,
                error=error,
                timestamp=time.time(),
            )
        )

    async def text(self, content: str, index: int = 0):
        """Emit TEXT_MESSAGE_CONTENT."""
        await self._emit(
            TextMessageContentEvent(
                contentBlockIndex=index,
                text=content,
                timestamp=time.time(),
            )
        )

    async def tool_call(
        self,
        tool_name: str,
        tool_input: Dict[str, Any],
        tool_use_id: Optional[str] = None,
    ):
        """Emit TOOL_CALL_START."""
        if tool_use_id is None:
            tool_use_id = f"tool-{int(time.time() * 1000)}"

        await self._emit(
            ToolCallStartEvent(
                toolUseId=tool_use_id,
                toolName=tool_name,
                toolInput=tool_input,
                timestamp=time.time(),
            )
        )

    async def tool_result(
        self,
        tool_use_id: str,
        result: str,
    ):
        """Emit TOOL_CALL_END."""
        await self._emit(
            ToolCallEndEvent(
                toolUseId=tool_use_id,
                toolResult=result,
                timestamp=time.time(),
            )
        )

    async def state_delta(self, operations: List[Dict[str, Any]]):
        """Emit STATE_DELTA (JSON-Patch operations)."""
        await self._emit(
            StateDeltaEvent(
                operations=operations,
                timestamp=time.time(),
            )
        )

    async def state_snapshot(self, state: Dict[str, Any]):
        """Emit STATE_SNAPSHOT."""
        await self._emit(
            StateSnapshotEvent(
                state=state,
                timestamp=time.time(),
            )
        )

    async def custom(self, widget_name: str, data: Dict[str, Any]):
        """Emit CUSTOM (generative UI widget)."""
        await self._emit(
            CustomEvent(
                name=widget_name,
                data=data,
                timestamp=time.time(),
            )
        )

    def get_queue(self) -> asyncio.Queue:
        """Get the event queue (for SSE handler)."""
        return self.queue

    def get_stats(self) -> Dict[str, Any]:
        """Get emitter statistics."""
        return {
            "conversation_id": self.conversation_id,
            "run_id": self.run_id,
            "events_emitted": self.events_emitted,
            "events_dropped": self.events_dropped,
            "queue_size": self.queue.qsize(),
            "elapsed_seconds": time.time() - self._start_time,
        }


class NoOpUIEmitter:
    """
    No-op UIEmitter for agents without generative_ui=True.

    All methods are async no-ops; doesn't block invoke().
    """

    def __init__(self, conversation_id: str):
        self.conversation_id = conversation_id

    async def run_started(self):
        pass

    async def run_finished(self):
        pass

    async def run_error(self, error: str):
        pass

    async def text(self, content: str, index: int = 0):
        pass

    async def tool_call(
        self,
        tool_name: str,
        tool_input: Dict[str, Any],
        tool_use_id: Optional[str] = None,
    ):
        pass

    async def tool_result(self, tool_use_id: str, result: str):
        pass

    async def state_delta(self, operations: List[Dict[str, Any]]):
        pass

    async def state_snapshot(self, state: Dict[str, Any]):
        pass

    async def custom(self, widget_name: str, data: Dict[str, Any]):
        pass

    def get_queue(self):
        return None

    def get_stats(self) -> Dict[str, Any]:
        return {"type": "noop"}
