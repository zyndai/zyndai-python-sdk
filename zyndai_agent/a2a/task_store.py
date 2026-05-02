"""In-memory task store with suspended-handler resume.

Mirrors `zyndai-ts-sdk/src/a2a/task-store.ts`, adapted to Python's sync
threading model since Flask routes run synchronously per worker thread.

Each Task is keyed by its A2A id; the store tracks:
  - the current Task object (state, history, artifacts)
  - SSE-stream subscribers (for message/stream)
  - registered push-notification config (for tasks/pushNotificationConfig/set)
  - a `_resume_slot` (threading.Event + payload) — used when a handler
    in one request thread calls task.ask() and another inbound request
    needs to wake it up with the next message in the same context.

Idle GC: tasks in `input-required` / `auth-required` past TTL transition
to `failed` and any subscribers are notified.
"""

import threading
import time
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from zyndai_agent.a2a.types import TERMINAL_STATES


_DEFAULT_IDLE_TTL_SECONDS = 60 * 60  # 1 hour
_TERMINAL_RETENTION_SECONDS = 5 * 60  # 5 min after terminal
_SWEEP_INTERVAL_SECONDS = 60


# Subscriber receives a stream event dict. Sync — runs on the broadcasting
# thread. Long-running subscribers should hand off via queue/threadpool.
StreamSubscriber = Callable[[dict[str, Any]], None]


class _ResumeSlot:
    """A thread-synchronization handle handed to a suspended handler.

    The handler thread calls `wait()` and blocks. A different request
    thread receiving a follow-up message calls `resolve(msg)` to wake
    the handler with the next inbound payload. A `cancel()` is provided
    for GC paths.
    """

    __slots__ = ("event", "value", "_canceled")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.value: Optional[dict[str, Any]] = None
        self._canceled = False

    def resolve(self, message: dict[str, Any]) -> None:
        if self.event.is_set():
            return
        self.value = message
        self.event.set()

    def cancel(self, reason: str = "canceled") -> None:
        if self.event.is_set():
            return
        self._canceled = True
        self.value = {"__abort__": reason}
        self.event.set()

    def wait(self, timeout: Optional[float] = None) -> dict[str, Any]:
        ok = self.event.wait(timeout=timeout)
        if not ok:
            raise TimeoutError("ask: timed out waiting for follow-up")
        if self._canceled:
            raise RuntimeError(f"ask: aborted ({self.value})")
        assert self.value is not None
        return self.value


class _TaskEntry:
    __slots__ = (
        "task",
        "subscribers",
        "resume_slot",
        "push_config",
        "last_activity",
        "terminal_at",
        "lock",
    )

    def __init__(self, task: dict[str, Any]) -> None:
        self.task: dict[str, Any] = task
        self.subscribers: set[StreamSubscriber] = set()
        self.resume_slot: Optional[_ResumeSlot] = None
        self.push_config: Optional[dict[str, Any]] = None
        self.last_activity: float = time.time()
        self.terminal_at: Optional[float] = None
        self.lock = threading.Lock()


class TaskStore:
    """In-memory task store. One per agent process.

    Thread-safe: a single `_tasks` lock guards the registry; per-entry
    locks guard mutations within a task.
    """

    def __init__(
        self,
        *,
        idle_ttl_seconds: int = _DEFAULT_IDLE_TTL_SECONDS,
    ) -> None:
        self._tasks: dict[str, _TaskEntry] = {}
        self._lock = threading.Lock()
        self._idle_ttl = idle_ttl_seconds
        self._sweep_thread: Optional[threading.Thread] = None
        self._closed = threading.Event()

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    def start_sweeper(self) -> None:
        """Begin the periodic GC sweeper. Daemon thread; safe to call
        multiple times.
        """
        if self._sweep_thread is not None or self._closed.is_set():
            return
        self._sweep_thread = threading.Thread(
            target=self._sweep_loop,
            name="ZyndA2ATaskSweeper",
            daemon=True,
        )
        self._sweep_thread.start()

    def shutdown(self) -> None:
        self._closed.set()
        # Cancel any suspended handlers so they don't hang forever.
        with self._lock:
            for entry in self._tasks.values():
                if entry.resume_slot is not None:
                    entry.resume_slot.cancel("store shutdown")

    @staticmethod
    def new_task_id() -> str:
        return f"task-{uuid.uuid4()}"

    @staticmethod
    def new_context_id() -> str:
        return f"ctx-{uuid.uuid4()}"

    # -------------------------------------------------------------------------
    # CRUD
    # -------------------------------------------------------------------------

    def has(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._tasks

    def get(self, task_id: str) -> Optional[dict[str, Any]]:
        """Returns a deep copy of the task dict so callers can't mutate
        the live state.
        """
        with self._lock:
            entry = self._tasks.get(task_id)
            if entry is None:
                return None
            return deepcopy(entry.task)

    def get_or_create(self, task_id: str, context_id: str) -> dict[str, Any]:
        """Returns the live task dict (mutable). Server holds it for
        the duration of a handler invocation.
        """
        with self._lock:
            entry = self._tasks.get(task_id)
            if entry is not None:
                entry.last_activity = time.time()
                return entry.task

            task: dict[str, Any] = {
                "kind": "task",
                "id": task_id,
                "contextId": context_id,
                "status": {"state": "submitted", "timestamp": _now_iso()},
                "artifacts": [],
                "history": [],
            }
            self._tasks[task_id] = _TaskEntry(task)
            return task

    # -------------------------------------------------------------------------
    # State transitions
    # -------------------------------------------------------------------------

    def set_state(
        self,
        task_id: str,
        state: str,
        message: Optional[dict[str, Any]] = None,
    ) -> None:
        with self._lock:
            entry = self._tasks.get(task_id)
            if entry is None:
                return
        with entry.lock:
            status: dict[str, Any] = {"state": state, "timestamp": _now_iso()}
            if message is not None:
                status["message"] = message
            entry.task["status"] = status
            entry.last_activity = time.time()
            if state in TERMINAL_STATES:
                entry.terminal_at = time.time()

            event = {
                "kind": "status-update",
                "taskId": entry.task["id"],
                "contextId": entry.task["contextId"],
                "status": status,
                "final": state in TERMINAL_STATES,
            }
        self._broadcast(entry, event)

    def append_message(self, task_id: str, message: dict[str, Any]) -> None:
        with self._lock:
            entry = self._tasks.get(task_id)
            if entry is None:
                return
        with entry.lock:
            entry.task.setdefault("history", []).append(message)
            entry.last_activity = time.time()

    def append_artifact(
        self,
        task_id: str,
        artifact: dict[str, Any],
        *,
        append: bool = False,
        last_chunk: bool = False,
    ) -> None:
        with self._lock:
            entry = self._tasks.get(task_id)
            if entry is None:
                return
        with entry.lock:
            artifacts: list[dict[str, Any]] = entry.task.setdefault("artifacts", [])
            if append:
                existing = next(
                    (a for a in artifacts if a.get("artifactId") == artifact["artifactId"]),
                    None,
                )
                if existing is not None:
                    existing.setdefault("parts", []).extend(artifact.get("parts") or [])
                else:
                    artifacts.append(artifact)
            else:
                replaced = False
                for i, a in enumerate(artifacts):
                    if a.get("artifactId") == artifact["artifactId"]:
                        artifacts[i] = artifact
                        replaced = True
                        break
                if not replaced:
                    artifacts.append(artifact)
            entry.last_activity = time.time()

            event: dict[str, Any] = {
                "kind": "artifact-update",
                "taskId": entry.task["id"],
                "contextId": entry.task["contextId"],
                "artifact": artifact,
            }
            if append:
                event["append"] = True
            if last_chunk:
                event["lastChunk"] = True
        self._broadcast(entry, event)

    # -------------------------------------------------------------------------
    # Subscribers (SSE / push-config)
    # -------------------------------------------------------------------------

    def subscribe(self, task_id: str, fn: StreamSubscriber) -> Callable[[], None]:
        with self._lock:
            entry = self._tasks.get(task_id)
            if entry is None:
                return lambda: None
            entry.subscribers.add(fn)

        def unsubscribe() -> None:
            entry.subscribers.discard(fn)

        return unsubscribe

    def set_push_config(self, task_id: str, cfg: dict[str, Any]) -> None:
        with self._lock:
            entry = self._tasks.get(task_id)
        if entry is not None:
            entry.push_config = cfg

    def get_push_config(self, task_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            entry = self._tasks.get(task_id)
        return entry.push_config if entry else None

    def _broadcast(self, entry: _TaskEntry, event: dict[str, Any]) -> None:
        for fn in list(entry.subscribers):
            try:
                fn(event)
            except Exception as e:  # pragma: no cover
                print(f"[task-store] subscriber threw: {e}")

    # -------------------------------------------------------------------------
    # Suspend / resume for input-required loopback
    # -------------------------------------------------------------------------

    def suspend_until_next_message(
        self, task_id: str, timeout_seconds: Optional[float] = None
    ) -> dict[str, Any]:
        """Block the calling handler thread until a follow-up
        `message/send` arrives in the same context. Returns the resumed
        message dict. Raises if the task is canceled or expires.

        Default timeout = idle TTL — handlers shouldn't hang forever.
        """
        with self._lock:
            entry = self._tasks.get(task_id)
            if entry is None:
                raise RuntimeError(f"task {task_id} not found")
            if entry.resume_slot is not None:
                raise RuntimeError(
                    f"task {task_id} already has a pending suspended handler"
                )
            slot = _ResumeSlot()
            entry.resume_slot = slot
        try:
            return slot.wait(timeout=timeout_seconds or self._idle_ttl)
        finally:
            with self._lock:
                if entry.resume_slot is slot:
                    entry.resume_slot = None

    def resume_if_suspended(self, task_id: str, message: dict[str, Any]) -> bool:
        """If a handler is suspended on this task, hand it the new
        message and return True; otherwise return False.
        """
        with self._lock:
            entry = self._tasks.get(task_id)
            if entry is None or entry.resume_slot is None:
                return False
            slot = entry.resume_slot
            entry.resume_slot = None
        slot.resolve(message)
        return True

    # -------------------------------------------------------------------------
    # GC sweep
    # -------------------------------------------------------------------------

    def _sweep_loop(self) -> None:
        while not self._closed.is_set():
            if self._closed.wait(timeout=_SWEEP_INTERVAL_SECONDS):
                return
            self._sweep_once()

    def _sweep_once(self) -> None:
        now = time.time()
        with self._lock:
            for task_id, entry in list(self._tasks.items()):
                if (
                    entry.terminal_at is not None
                    and now - entry.terminal_at > _TERMINAL_RETENTION_SECONDS
                ):
                    self._tasks.pop(task_id, None)
                    continue
                if (
                    entry.task.get("status", {}).get("state") not in TERMINAL_STATES
                    and now - entry.last_activity > self._idle_ttl
                ):
                    self.set_state(
                        task_id,
                        "failed",
                        {
                            "kind": "message",
                            "messageId": str(uuid.uuid4()),
                            "role": "agent",
                            "parts": [
                                {
                                    "kind": "text",
                                    "text": f"Task timed out after {self._idle_ttl}s of inactivity",
                                }
                            ],
                        },
                    )
                    if entry.resume_slot is not None:
                        entry.resume_slot.cancel("idle timeout")


def _now_iso() -> str:
    return (
        datetime.now(tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
