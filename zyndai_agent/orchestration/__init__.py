"""
Orchestration primitives for multi-agent coordination.

Provides task tracking, parallel fan-out dispatch, and a strategy-based
Coordinator API for building agents that orchestrate other agents.
"""

from zyndai_agent.orchestration.task import Task, TaskStatus, TaskTracker

__all__ = [
    "Task",
    "TaskStatus",
    "TaskTracker",
]

# Fan-out and Coordinator are imported lazily to avoid circular deps
# at module level. They are added to __all__ and importable directly:
#   from zyndai_agent.orchestration import Coordinator, fan_out


def __getattr__(name: str):
    if name in ("fan_out", "FanOutResult"):
        from zyndai_agent.orchestration.fan_out import fan_out, FanOutResult
        return fan_out if name == "fan_out" else FanOutResult
    if name in ("Coordinator", "OrchestrationContext"):
        from zyndai_agent.orchestration.coordinator import Coordinator, OrchestrationContext
        return Coordinator if name == "Coordinator" else OrchestrationContext
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
