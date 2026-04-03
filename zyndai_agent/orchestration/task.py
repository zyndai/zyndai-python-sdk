"""
Task state machine for tracking orchestrated work units.

Each Task represents a single unit of work dispatched to an agent.
TaskTracker provides CRUD and aggregate queries over a collection of tasks.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


@dataclass
class Task:
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    description: str = ""
    assigned_to: str | None = None
    status: TaskStatus = TaskStatus.PENDING
    result: dict[str, Any] | None = None
    usage: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    timeout_seconds: float = 60.0
    max_budget_usd: float = 1.0

    def mark_running(self) -> None:
        self.status = TaskStatus.RUNNING
        self.started_at = datetime.now(timezone.utc)

    def mark_completed(self, result: dict[str, Any], usage: dict[str, Any] | None = None) -> None:
        self.status = TaskStatus.COMPLETED
        self.result = result
        self.usage = usage
        self.completed_at = datetime.now(timezone.utc)

    def mark_failed(self, error: str) -> None:
        self.status = TaskStatus.FAILED
        self.error = error
        self.completed_at = datetime.now(timezone.utc)

    def mark_cancelled(self) -> None:
        self.status = TaskStatus.CANCELLED
        self.completed_at = datetime.now(timezone.utc)

    def mark_timed_out(self) -> None:
        self.status = TaskStatus.TIMED_OUT
        self.error = f"Timed out after {self.timeout_seconds}s"
        self.completed_at = datetime.now(timezone.utc)

    @property
    def is_terminal(self) -> bool:
        return self.status in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.TIMED_OUT,
        )

    @property
    def duration_ms(self) -> float | None:
        if self.started_at is None:
            return None
        end = self.completed_at or datetime.now(timezone.utc)
        return (end - self.started_at).total_seconds() * 1000


class TaskTracker:
    """Thread-safe tracker for a collection of tasks."""

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._lock = threading.RLock()

    def create_task(
        self,
        description: str,
        assigned_to: str | None = None,
        timeout_seconds: float = 60.0,
        max_budget_usd: float = 1.0,
    ) -> Task:
        task = Task(
            description=description,
            assigned_to=assigned_to,
            timeout_seconds=timeout_seconds,
            max_budget_usd=max_budget_usd,
        )
        with self._lock:
            self._tasks[task.task_id] = task
        return task

    def get_task(self, task_id: str) -> Task | None:
        with self._lock:
            return self._tasks.get(task_id)

    def active_tasks(self) -> list[Task]:
        with self._lock:
            return [t for t in self._tasks.values() if not t.is_terminal]

    def completed_tasks(self) -> list[Task]:
        with self._lock:
            return [t for t in self._tasks.values() if t.status == TaskStatus.COMPLETED]

    def total_cost(self) -> float:
        with self._lock:
            total = 0.0
            for t in self._tasks.values():
                if t.usage:
                    total += t.usage.get("cost_usd", 0.0)
            return total

    def summary(self) -> dict[str, Any]:
        with self._lock:
            by_status: dict[str, int] = {}
            for t in self._tasks.values():
                by_status[t.status.value] = by_status.get(t.status.value, 0) + 1
            return {
                "total": len(self._tasks),
                "by_status": by_status,
                "total_cost_usd": self.total_cost(),
            }
