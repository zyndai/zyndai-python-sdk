"""AG-UI protocol event types and serialization.

Reference: https://github.com/ag-ui-protocol/ag-ui
"""

import json
from typing import Any, Dict, Optional, List
from dataclasses import dataclass, asdict


@dataclass
class AGUIEvent:
    """Base AG-UI event."""

    type: str

    def to_json(self) -> str:
        """Serialize to JSON line for SSE."""
        data = asdict(self)
        return json.dumps(data)


@dataclass
class RunStartedEvent(AGUIEvent):
    """RUN_STARTED: workflow begins."""

    type: str = "RUN_STARTED"
    runId: str = ""
    timestamp: Optional[float] = None


@dataclass
class RunFinishedEvent(AGUIEvent):
    """RUN_FINISHED: workflow ends."""

    type: str = "RUN_FINISHED"
    runId: str = ""
    timestamp: Optional[float] = None
    elapsedMs: Optional[int] = None


@dataclass
class RunErrorEvent(AGUIEvent):
    """RUN_ERROR: workflow failed."""

    type: str = "RUN_ERROR"
    runId: str = ""
    error: str = ""
    timestamp: Optional[float] = None


@dataclass
class TextMessageContentEvent(AGUIEvent):
    """TEXT_MESSAGE_CONTENT: streaming text."""

    type: str = "TEXT_MESSAGE_CONTENT"
    contentBlockIndex: int = 0
    text: str = ""
    timestamp: Optional[float] = None


@dataclass
class ToolCallStartEvent(AGUIEvent):
    """TOOL_CALL_START: tool invocation begins."""

    type: str = "TOOL_CALL_START"
    toolUseId: str = ""
    toolName: str = ""
    toolInput: Dict[str, Any] = None
    timestamp: Optional[float] = None

    def __post_init__(self):
        if self.toolInput is None:
            self.toolInput = {}


@dataclass
class ToolCallEndEvent(AGUIEvent):
    """TOOL_CALL_END: tool invocation completes."""

    type: str = "TOOL_CALL_END"
    toolUseId: str = ""
    toolResult: Optional[str] = None
    timestamp: Optional[float] = None


@dataclass
class StateDeltaEvent(AGUIEvent):
    """STATE_DELTA: JSON-Patch state update."""

    type: str = "STATE_DELTA"
    operations: List[Dict[str, Any]] = None
    timestamp: Optional[float] = None

    def __post_init__(self):
        if self.operations is None:
            self.operations = []


@dataclass
class StateSnapshotEvent(AGUIEvent):
    """STATE_SNAPSHOT: full state snapshot."""

    type: str = "STATE_SNAPSHOT"
    state: Dict[str, Any] = None
    timestamp: Optional[float] = None

    def __post_init__(self):
        if self.state is None:
            self.state = {}


@dataclass
class CustomEvent(AGUIEvent):
    """CUSTOM: generative UI widget."""

    type: str = "CUSTOM"
    name: str = ""
    data: Dict[str, Any] = None
    timestamp: Optional[float] = None

    def __post_init__(self):
        if self.data is None:
            self.data = {}
