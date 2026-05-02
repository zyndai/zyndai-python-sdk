"""A2A server — Flask routes mounted at the agent's base URL.

Mirrors `zyndai-ts-sdk/src/a2a/server.ts`.

Routes:
  POST /a2a/v1
    - JSON-RPC 2.0 entry → dispatched on `method`.
    - For `message/stream`, returns text/event-stream and writes one
      JSON-RPC response per SSE frame until terminal state.
  GET  /.well-known/agent-card.json   — A2A-shaped Agent Card
  GET  /health                         — liveness probe
"""

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Optional, Type

import requests
from flask import Blueprint, Flask, Response, request, stream_with_context
from pydantic import BaseModel, ValidationError

from zyndai_agent.a2a.adapter import (
    Attachment,
    InboundMessage,
    coerce_handler_output,
    from_a2a_message,
    to_a2a_message,
)
from zyndai_agent.a2a.auth import (
    AuthMode,
    ReplayCache,
    ZyndAuthError,
    sign_message,
    verify_message,
)
from zyndai_agent.a2a.task_store import TaskStore
from zyndai_agent.a2a.types import (
    A2A_TASK_NOT_CANCELABLE,
    A2A_TASK_NOT_FOUND,
    INTERRUPTED_STATES,
    RPC_INTERNAL_ERROR,
    RPC_INVALID_PARAMS,
    RPC_INVALID_REQUEST,
    RPC_METHOD_NOT_FOUND,
    RPC_PARSE_ERROR,
    TERMINAL_STATES,
    ZYND_AUTH_EXPIRED,
    ZYND_AUTH_FAILED,
    ZYND_REPLAY_DETECTED,
    MessageSendParams,
    TaskIdParams,
    TaskPushNotificationConfig,
    TaskQueryParams,
)
from zyndai_agent.ed25519_identity import Ed25519Keypair


_log = logging.getLogger("zyndai.a2a.server")


DEFAULT_MAX_BODY_BYTES = 25 * 1024 * 1024


# -----------------------------------------------------------------------------
# Public types
# -----------------------------------------------------------------------------


@dataclass
class HandlerInput:
    """What the handler receives. `payload` is the validated payload
    when a payload_model is configured.
    """

    message: Any  # AgentMessage
    payload: dict[str, Any]
    attachments: list[Attachment]
    from_agent: bool
    signed: bool
    sender_entity_id: Optional[str]
    sender_fqan: Optional[str]


class TaskHandle:
    """Handle handed to the user's handler. Wraps the TaskStore for
    one specific task and exposes update/ask/complete/fail/cancel.
    """

    HANDLER_DONE_SENTINEL = {"__zynd_done": True}

    def __init__(self, server: "A2AServer", task_id: str, context_id: str) -> None:
        self._server = server
        self._task_id = task_id
        self._context_id = context_id

    @property
    def id(self) -> str:
        return self._task_id

    @property
    def context_id(self) -> str:
        return self._context_id

    @property
    def state(self) -> str:
        task = self._server.task_store.get(self._task_id)
        return (task or {}).get("status", {}).get("state", "unknown")

    def update(self, state: str, *, text: Optional[str] = None) -> None:
        msg = self._server._agent_message(text, self._context_id, self._task_id) if text else None
        self._server.task_store.set_state(self._task_id, state, msg)

    def emit_artifact(
        self,
        artifact: dict[str, Any],
        *,
        append: bool = False,
        last_chunk: bool = False,
    ) -> None:
        if "artifactId" not in artifact:
            artifact = {**artifact, "artifactId": str(uuid.uuid4())}
        self._server.task_store.append_artifact(
            self._task_id, artifact, append=append, last_chunk=last_chunk
        )

    def ask(
        self,
        question: str,
        *,
        data: Optional[dict[str, Any]] = None,
        timeout_seconds: Optional[float] = None,
    ) -> HandlerInput:
        """Transition the task to `input-required` and BLOCK the
        calling thread until the next inbound message arrives in this
        context. Returns the resumed HandlerInput.
        """
        ask_msg = self._server._agent_message(
            question, self._context_id, self._task_id, data=data
        )
        self._server.task_store.set_state(self._task_id, "input-required", ask_msg)

        reply_msg = self._server.task_store.suspend_until_next_message(
            self._task_id, timeout_seconds=timeout_seconds
        )
        if reply_msg.get("__abort__"):
            raise RuntimeError(f"ask: aborted ({reply_msg['__abort__']})")
        # The inbound dispatcher already verified x-zynd-auth before
        # routing the message to us; no need to re-verify here.
        inbound = from_a2a_message(reply_msg, self._server.payload_model)
        self._server.task_store.set_state(self._task_id, "working")
        self._server.task_store.append_message(self._task_id, reply_msg)
        auth = (reply_msg.get("metadata") or {}).get("x-zynd-auth") or {}
        return HandlerInput(
            message=inbound.message,
            payload=inbound.payload,
            attachments=inbound.attachments,
            from_agent=inbound.from_agent,
            signed=bool(reply_msg.get("metadata", {}).get("x-zynd-auth")),
            sender_entity_id=auth.get("entity_id"),
            sender_fqan=auth.get("fqan"),
        )

    def require_auth(
        self,
        scheme: str,
        details: Optional[dict[str, Any]] = None,
        *,
        timeout_seconds: Optional[float] = None,
    ) -> HandlerInput:
        ask_msg = self._server._agent_message(
            f"Authentication required ({scheme})",
            self._context_id,
            self._task_id,
            data={"authScheme": scheme, **(details or {})},
        )
        self._server.task_store.set_state(self._task_id, "auth-required", ask_msg)
        reply_msg = self._server.task_store.suspend_until_next_message(
            self._task_id, timeout_seconds=timeout_seconds
        )
        inbound = from_a2a_message(reply_msg, self._server.payload_model)
        self._server.task_store.set_state(self._task_id, "working")
        self._server.task_store.append_message(self._task_id, reply_msg)
        return HandlerInput(
            message=inbound.message,
            payload=inbound.payload,
            attachments=inbound.attachments,
            from_agent=inbound.from_agent,
            signed=bool(reply_msg.get("metadata", {}).get("x-zynd-auth")),
            sender_entity_id=None,
            sender_fqan=None,
        )

    def complete(self, result: Any) -> dict[str, Any]:
        """Mark the task `completed` with this final result. Validates
        against output_model if set. Returns the sentinel.
        """
        out = coerce_handler_output(result)

        if self._server.output_model is not None and out.get("data"):
            try:
                validated = self._server.output_model.model_validate(out["data"])
                out["data"] = validated.model_dump()
            except ValidationError as e:
                return self.fail(
                    f"handler output failed {self._server.output_model.__name__} validation: {e}"
                )

        artifact: dict[str, Any] = {
            "artifactId": str(uuid.uuid4()),
            "name": "result",
            "parts": [],
        }
        if out.get("data"):
            artifact["parts"].append({"kind": "data", "data": out["data"]})
        if out.get("text"):
            artifact["parts"].append({"kind": "text", "text": out["text"]})
        if out.get("attachments"):
            for att in out["attachments"]:
                if isinstance(att, Attachment):
                    file_dict = (
                        {"bytes": att.data, "name": att.filename, "mimeType": att.mime_type}
                        if att.data is not None
                        else {"uri": att.url, "name": att.filename, "mimeType": att.mime_type}
                    )
                else:
                    file_dict = att
                artifact["parts"].append({"kind": "file", "file": file_dict})
        if not artifact["parts"]:
            artifact["parts"].append({"kind": "text", "text": ""})
        self._server.task_store.append_artifact(self._task_id, artifact)
        self._server.task_store.set_state(self._task_id, "completed")
        return self.HANDLER_DONE_SENTINEL

    def fail(self, reason: str) -> dict[str, Any]:
        msg = self._server._agent_message(reason, self._context_id, self._task_id)
        self._server.task_store.set_state(self._task_id, "failed", msg)
        return self.HANDLER_DONE_SENTINEL

    def cancel(self) -> dict[str, Any]:
        msg = self._server._agent_message("Task canceled", self._context_id, self._task_id)
        self._server.task_store.set_state(self._task_id, "canceled", msg)
        return self.HANDLER_DONE_SENTINEL


Handler = Callable[[HandlerInput, TaskHandle], Any]


# -----------------------------------------------------------------------------
# Server
# -----------------------------------------------------------------------------


class A2AServer:
    """A2A protocol server.

    Wraps a Flask app (or registers a Blueprint on an existing one) with
    the A2A wire endpoints. Handler runs on the request thread; thread-safe
    suspend/resume via the underlying TaskStore.
    """

    def __init__(
        self,
        *,
        entity_id: str,
        keypair: Ed25519Keypair,
        agent_card_builder: Callable[[], dict[str, Any]],
        host: str = "0.0.0.0",
        port: int = 5000,
        a2a_path: str = "/a2a/v1",
        auth_mode: AuthMode = "permissive",
        max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
        payload_model: Optional[Type[BaseModel]] = None,
        output_model: Optional[Type[BaseModel]] = None,
        fqan: Optional[str] = None,
        developer_proof: Optional[dict[str, Any]] = None,
        idle_ttl_seconds: int = 60 * 60,
        flask_app: Optional[Flask] = None,
    ) -> None:
        self.entity_id = entity_id
        self.keypair = keypair
        self.agent_card_builder = agent_card_builder
        self.host = host
        self.port = port
        self.a2a_path = a2a_path
        self.auth_mode: AuthMode = auth_mode
        self.max_body_bytes = max_body_bytes
        self.payload_model = payload_model
        self.output_model = output_model
        self.fqan = fqan
        self.developer_proof = developer_proof

        self.task_store = TaskStore(idle_ttl_seconds=idle_ttl_seconds)
        self.replay_cache = ReplayCache()

        self._handler: Optional[Handler] = None
        self._app = flask_app or Flask(f"zynd-a2a-{entity_id[:12]}")
        self._app.config["MAX_CONTENT_LENGTH"] = self.max_body_bytes
        self._app.logger.setLevel(logging.ERROR)
        self._register_routes()

        self._server_thread: Optional[threading.Thread] = None
        self._is_running = False
        self._bound_port = 0

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    def set_handler(self, fn: Handler) -> None:
        self._handler = fn

    @property
    def app(self) -> Flask:
        return self._app

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def bound_port(self) -> int:
        return self._bound_port

    @property
    def a2a_url(self) -> str:
        host = "127.0.0.1" if self.host == "0.0.0.0" else self.host
        return f"http://{host}:{self._bound_port or self.port}{self.a2a_path}"

    def start(self) -> None:
        """Start the Flask dev server in a daemon thread + the task-store
        sweeper. Probes the port to fail fast on EADDRINUSE.
        """
        if self._is_running:
            return

        # Fail-fast port probe.
        import socket

        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((self.host, self.port))
        except OSError as e:
            probe.close()
            raise RuntimeError(
                f"Cannot start A2A server: port {self.port} on {self.host} is "
                f"already in use ({e}). Stop the process using it or pick a "
                f"different port."
            ) from e
        finally:
            probe.close()

        self.task_store.start_sweeper()

        def run() -> None:
            self._app.run(
                host=self.host,
                port=self.port,
                debug=False,
                use_reloader=False,
                threaded=True,
            )

        self._server_thread = threading.Thread(
            target=run, daemon=True, name=f"A2AServer-{self.entity_id[:12]}"
        )
        self._server_thread.start()
        self._is_running = True
        self._bound_port = self.port
        time.sleep(0.5)  # give Flask a moment to start accepting

    def stop(self) -> None:
        self.task_store.shutdown()
        self._is_running = False
        # Flask dev server doesn't have a clean stop API; rely on daemon
        # thread to die with the process. For production, you'd run
        # under gunicorn/uvicorn and stop those at the process level.

    # -------------------------------------------------------------------------
    # Routes
    # -------------------------------------------------------------------------

    def _register_routes(self) -> None:
        bp = Blueprint(f"a2a-{self.entity_id[:12]}", __name__)

        @bp.route("/health", methods=["GET"])
        def health() -> Any:
            from flask import jsonify

            return jsonify(
                {
                    "status": "ok",
                    "entity_id": self.entity_id,
                    "timestamp": _now_iso(),
                }
            )

        @bp.route("/.well-known/agent-card.json", methods=["GET"])
        def agent_card() -> Any:
            from flask import jsonify

            try:
                return jsonify(self.agent_card_builder())
            except Exception as e:
                return jsonify({"error": str(e)}), 500

        @bp.route(self.a2a_path, methods=["POST"])
        def a2a() -> Any:
            return self._handle_rpc()

        self._app.register_blueprint(bp)

    def _handle_rpc(self) -> Any:
        from flask import jsonify

        try:
            body = request.get_json(silent=False)
        except Exception:
            return jsonify(self._rpc_error(None, RPC_PARSE_ERROR, "Parse error")), 200

        if not body or body.get("jsonrpc") != "2.0" or not isinstance(body.get("method"), str):
            return jsonify(
                self._rpc_error(
                    body.get("id") if isinstance(body, dict) else None,
                    RPC_INVALID_REQUEST,
                    "Not a valid JSON-RPC 2.0 request",
                )
            ), 200

        rpc_id = body.get("id")
        method = body["method"]
        params = body.get("params")

        try:
            if method == "message/send":
                return self._handle_message_send(rpc_id, params)
            if method == "message/stream":
                return self._handle_message_stream(rpc_id, params)
            if method == "tasks/get":
                return jsonify(self._handle_tasks_get(rpc_id, params))
            if method == "tasks/cancel":
                return jsonify(self._handle_tasks_cancel(rpc_id, params))
            if method == "tasks/resubscribe":
                return self._handle_tasks_resubscribe(rpc_id, params)
            if method == "tasks/pushNotificationConfig/set":
                return jsonify(self._handle_push_set(rpc_id, params))
            if method == "tasks/pushNotificationConfig/get":
                return jsonify(self._handle_push_get(rpc_id, params))
            return jsonify(
                self._rpc_error(rpc_id, RPC_METHOD_NOT_FOUND, f"Method not found: {method}")
            )
        except A2ARpcError as e:
            return jsonify(self._rpc_error(rpc_id, e.code, e.message, e.data))
        except Exception as e:
            _log.exception("[a2a-server] dispatch threw")
            return jsonify(self._rpc_error(rpc_id, RPC_INTERNAL_ERROR, str(e)))

    # -------------------------------------------------------------------------
    # message/send (synchronous)
    # -------------------------------------------------------------------------

    def _handle_message_send(self, rpc_id: Any, params: Any) -> Any:
        from flask import jsonify

        try:
            parsed = MessageSendParams.model_validate(params)
        except ValidationError as e:
            return jsonify(
                self._rpc_error(rpc_id, RPC_INVALID_PARAMS, "Invalid params", e.errors())
            )

        message = parsed.message.model_dump(by_alias=False, exclude_none=True)
        # Re-attach raw metadata exactly as it arrived (Pydantic round-trip
        # can lose non-modeled extension fields like x-zynd-auth).
        if isinstance(params, dict):
            raw_meta = ((params.get("message") or {}).get("metadata")) or {}
            if raw_meta:
                message.setdefault("metadata", {}).update(raw_meta)

        # Verify x-zynd-auth.
        try:
            auth_ctx = verify_message(
                message, mode=self.auth_mode, replay_cache=self.replay_cache
            )
        except ZyndAuthError as e:
            code = (
                ZYND_REPLAY_DETECTED
                if e.reason == "replay_detected"
                else ZYND_AUTH_EXPIRED
                if e.reason == "expired_or_skewed"
                else ZYND_AUTH_FAILED
            )
            return jsonify(self._rpc_error(rpc_id, code, str(e)))

        task_id = message.get("taskId") or self.task_store.new_task_id()
        context_id = message.get("contextId") or self.task_store.new_context_id()
        task = self.task_store.get_or_create(task_id, context_id)

        # Pick up an inline pushNotificationConfig if the caller passed one
        # in `params.configuration`. Saves a separate
        # `tasks/pushNotificationConfig/set` round trip for fire-and-forget
        # callers, and is what the A2A spec permits in MessageSendConfiguration.
        self._maybe_set_inline_push_config(task_id, parsed.configuration)

        # If suspended in input-required, hand the message to the suspended handler.
        if task["status"]["state"] in INTERRUPTED_STATES:
            resumed = self.task_store.resume_if_suspended(task_id, message)
            if resumed:
                self._wait_for_settle(task_id)
                final = self.task_store.get(task_id)
                return jsonify({"jsonrpc": "2.0", "id": rpc_id, "result": final})

        # Fresh dispatch.
        self.task_store.append_message(task_id, message)
        self.task_store.set_state(task_id, "working")

        # Run the handler on a worker thread so we can wait for it to
        # settle (terminal or interrupted) without blocking the request
        # thread holding handler-side suspend slots.
        threading.Thread(
            target=self._dispatch,
            args=(task_id, context_id, message, auth_ctx),
            name=f"A2AHandler-{task_id[:12]}",
            daemon=True,
        ).start()

        self._wait_for_settle(task_id)
        final = self.task_store.get(task_id)
        return jsonify({"jsonrpc": "2.0", "id": rpc_id, "result": final})

    def _maybe_set_inline_push_config(
        self, task_id: str, configuration: Optional[dict[str, Any]]
    ) -> None:
        """Honor `params.configuration.pushNotificationConfig` when present.

        A2A spec allows the caller to register a callback URL inline with
        message/send so they don't need a separate `tasks/pushNotificationConfig/set`
        round-trip. We accept both the camelCase (spec) and snake_case forms
        because Python clients often serialize via field aliases.
        """
        if not isinstance(configuration, dict):
            return
        cfg = (
            configuration.get("pushNotificationConfig")
            or configuration.get("push_notification_config")
        )
        if not isinstance(cfg, dict):
            return
        url = cfg.get("url")
        if not isinstance(url, str) or not url:
            return
        self.task_store.set_push_config(task_id, cfg)

    # -------------------------------------------------------------------------
    # message/stream (SSE)
    # -------------------------------------------------------------------------

    def _handle_message_stream(self, rpc_id: Any, params: Any) -> Any:
        from flask import jsonify

        try:
            parsed = MessageSendParams.model_validate(params)
        except ValidationError as e:
            return jsonify(
                self._rpc_error(rpc_id, RPC_INVALID_PARAMS, "Invalid params", e.errors())
            )

        message = parsed.message.model_dump(by_alias=False, exclude_none=True)
        if isinstance(params, dict):
            raw_meta = ((params.get("message") or {}).get("metadata")) or {}
            if raw_meta:
                message.setdefault("metadata", {}).update(raw_meta)

        try:
            auth_ctx = verify_message(
                message, mode=self.auth_mode, replay_cache=self.replay_cache
            )
        except ZyndAuthError as e:
            return jsonify(self._rpc_error(rpc_id, ZYND_AUTH_FAILED, str(e)))

        task_id = message.get("taskId") or self.task_store.new_task_id()
        context_id = message.get("contextId") or self.task_store.new_context_id()
        self.task_store.get_or_create(task_id, context_id)
        self.task_store.append_message(task_id, message)

        # Inline pushNotificationConfig — same shortcut as message/send.
        self._maybe_set_inline_push_config(task_id, parsed.configuration)

        # Subscribe before kicking off dispatch so we don't miss the
        # initial `working` transition.
        event_queue: list[dict[str, Any]] = []
        queue_lock = threading.Lock()
        queue_cond = threading.Condition(queue_lock)
        finished = threading.Event()

        def on_event(ev: dict[str, Any]) -> None:
            with queue_cond:
                event_queue.append(ev)
                if (
                    ev.get("kind") == "status-update"
                    and ev.get("final")
                ):
                    finished.set()
                queue_cond.notify_all()

        unsubscribe = self.task_store.subscribe(task_id, on_event)
        self.task_store.set_state(task_id, "working")
        threading.Thread(
            target=self._dispatch,
            args=(task_id, context_id, message, auth_ctx),
            name=f"A2AHandler-{task_id[:12]}",
            daemon=True,
        ).start()

        def gen() -> Any:
            try:
                while True:
                    with queue_cond:
                        while not event_queue and not finished.is_set():
                            queue_cond.wait(timeout=1.0)
                        events = event_queue[:]
                        event_queue.clear()
                    for ev in events:
                        frame = json.dumps({"jsonrpc": "2.0", "id": rpc_id, "result": ev})
                        yield f"data: {frame}\n\n"
                    if finished.is_set() and not event_queue:
                        return
            finally:
                unsubscribe()

        return Response(stream_with_context(gen()), mimetype="text/event-stream")

    # -------------------------------------------------------------------------
    # tasks/get, cancel, resubscribe
    # -------------------------------------------------------------------------

    def _handle_tasks_get(self, rpc_id: Any, params: Any) -> dict[str, Any]:
        try:
            parsed = TaskQueryParams.model_validate(params)
        except ValidationError as e:
            return self._rpc_error(rpc_id, RPC_INVALID_PARAMS, "Invalid params", e.errors())
        task = self.task_store.get(parsed.id)
        if task is None:
            return self._rpc_error(rpc_id, A2A_TASK_NOT_FOUND, f"Task {parsed.id} not found")
        return {"jsonrpc": "2.0", "id": rpc_id, "result": task}

    def _handle_tasks_cancel(self, rpc_id: Any, params: Any) -> dict[str, Any]:
        try:
            parsed = TaskIdParams.model_validate(params)
        except ValidationError as e:
            return self._rpc_error(rpc_id, RPC_INVALID_PARAMS, "Invalid params", e.errors())
        task = self.task_store.get(parsed.id)
        if task is None:
            return self._rpc_error(rpc_id, A2A_TASK_NOT_FOUND, f"Task {parsed.id} not found")
        if task["status"]["state"] in TERMINAL_STATES:
            return self._rpc_error(
                rpc_id,
                A2A_TASK_NOT_CANCELABLE,
                f"Task already in terminal state {task['status']['state']}",
            )
        self.task_store.set_state(parsed.id, "canceled")
        return {"jsonrpc": "2.0", "id": rpc_id, "result": self.task_store.get(parsed.id)}

    def _handle_tasks_resubscribe(self, rpc_id: Any, params: Any) -> Any:
        from flask import jsonify

        try:
            parsed = TaskIdParams.model_validate(params)
        except ValidationError as e:
            return jsonify(
                self._rpc_error(rpc_id, RPC_INVALID_PARAMS, "Invalid params", e.errors())
            )
        if not self.task_store.has(parsed.id):
            return jsonify(
                self._rpc_error(rpc_id, A2A_TASK_NOT_FOUND, f"Task {parsed.id} not found")
            )

        event_queue: list[dict[str, Any]] = []
        queue_lock = threading.Lock()
        queue_cond = threading.Condition(queue_lock)
        finished = threading.Event()

        def on_event(ev: dict[str, Any]) -> None:
            with queue_cond:
                event_queue.append(ev)
                if ev.get("kind") == "status-update" and ev.get("final"):
                    finished.set()
                queue_cond.notify_all()

        # Send current task state as the first event so the resubscriber catches up.
        cur = self.task_store.get(parsed.id)
        if cur:
            event_queue.append({"kind": "task", "task": cur})
            if cur["status"]["state"] in TERMINAL_STATES:
                finished.set()

        unsubscribe = self.task_store.subscribe(parsed.id, on_event)

        def gen() -> Any:
            try:
                while True:
                    with queue_cond:
                        while not event_queue and not finished.is_set():
                            queue_cond.wait(timeout=1.0)
                        events = event_queue[:]
                        event_queue.clear()
                    for ev in events:
                        frame = json.dumps({"jsonrpc": "2.0", "id": rpc_id, "result": ev})
                        yield f"data: {frame}\n\n"
                    if finished.is_set() and not event_queue:
                        return
            finally:
                unsubscribe()

        return Response(stream_with_context(gen()), mimetype="text/event-stream")

    # -------------------------------------------------------------------------
    # push notification config
    # -------------------------------------------------------------------------

    def _handle_push_set(self, rpc_id: Any, params: Any) -> dict[str, Any]:
        try:
            parsed = TaskPushNotificationConfig.model_validate(params)
        except ValidationError as e:
            return self._rpc_error(rpc_id, RPC_INVALID_PARAMS, "Invalid params", e.errors())
        if not self.task_store.has(parsed.taskId):
            return self._rpc_error(
                rpc_id, A2A_TASK_NOT_FOUND, f"Task {parsed.taskId} not found"
            )
        self.task_store.set_push_config(
            parsed.taskId, parsed.pushNotificationConfig.model_dump(exclude_none=True)
        )
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": parsed.model_dump(exclude_none=True),
        }

    def _handle_push_get(self, rpc_id: Any, params: Any) -> dict[str, Any]:
        try:
            parsed = TaskIdParams.model_validate(params)
        except ValidationError as e:
            return self._rpc_error(rpc_id, RPC_INVALID_PARAMS, "Invalid params", e.errors())
        cfg = self.task_store.get_push_config(parsed.id)
        if cfg is None:
            return self._rpc_error(
                rpc_id, A2A_TASK_NOT_FOUND, f"No push config for task {parsed.id}"
            )
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {"taskId": parsed.id, "pushNotificationConfig": cfg},
        }

    # -------------------------------------------------------------------------
    # Dispatch
    # -------------------------------------------------------------------------

    def _dispatch(
        self,
        task_id: str,
        context_id: str,
        message: dict[str, Any],
        auth_ctx: dict[str, Any],
    ) -> None:
        if self._handler is None:
            self.task_store.set_state(
                task_id,
                "rejected",
                self._error_message("No handler is registered on this agent"),
            )
            return

        try:
            inbound = from_a2a_message(message, self.payload_model)
        except ValidationError as e:
            self.task_store.set_state(
                task_id,
                "rejected",
                self._error_message(f"payload validation failed: {e}"),
            )
            return

        auth = (message.get("metadata") or {}).get("x-zynd-auth") or {}
        handler_input = HandlerInput(
            message=inbound.message,
            payload=inbound.payload,
            attachments=inbound.attachments,
            from_agent=inbound.from_agent,
            signed=bool(auth_ctx.get("signed")),
            sender_entity_id=auth_ctx.get("entity_id"),
            sender_fqan=auth_ctx.get("fqan"),
        )
        handle = TaskHandle(self, task_id, context_id)

        try:
            ret = self._handler(handler_input, handle)
            if isinstance(ret, dict) and ret.get("__zynd_done") is True:
                # Handler explicitly drove the task to a terminal state.
                pass
            else:
                cur = self.task_store.get(task_id)
                if cur and cur["status"]["state"] not in TERMINAL_STATES:
                    handle.complete(ret)
        except Exception as e:
            _log.exception("[a2a-server] handler threw")
            cur = self.task_store.get(task_id)
            if cur and cur["status"]["state"] not in TERMINAL_STATES:
                handle.fail(str(e))

        self._deliver_push_if_configured(task_id)

    def _deliver_push_if_configured(self, task_id: str) -> None:
        cfg = self.task_store.get_push_config(task_id)
        if cfg is None:
            return
        task = self.task_store.get(task_id)
        if task is None:
            return
        state = task["status"]["state"]
        if state not in TERMINAL_STATES and state not in INTERRUPTED_STATES:
            return

        event = {
            "kind": "status-update",
            "taskId": task["id"],
            "contextId": task["contextId"],
            "status": task["status"],
            "final": state in TERMINAL_STATES,
        }
        wrapper = to_a2a_message(
            role="agent",
            message_id=str(uuid.uuid4()),
            data=event,
            task_id=task["id"],
            context_id=task["contextId"],
        )
        sign_message(
            wrapper,
            self.keypair,
            self.entity_id,
            fqan=self.fqan,
            developer_proof=self.developer_proof,
        )

        headers = {"Content-Type": "application/json"}
        if cfg.get("token"):
            headers["X-A2A-Notification-Token"] = cfg["token"]
        try:
            requests.post(cfg["url"], json=wrapper, headers=headers, timeout=10)
        except Exception as e:
            _log.warning(f"[a2a-server] push delivery failed for task {task_id}: {e}")

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _agent_message(
        self,
        text: Optional[str],
        context_id: str,
        task_id: str,
        *,
        data: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        msg = to_a2a_message(
            role="agent",
            message_id=str(uuid.uuid4()),
            context_id=context_id,
            task_id=task_id,
            text=text,
            data=data,
        )
        sign_message(
            msg,
            self.keypair,
            self.entity_id,
            fqan=self.fqan,
            developer_proof=self.developer_proof,
        )
        return msg

    def _error_message(self, reason: str) -> dict[str, Any]:
        return self._agent_message(reason, "", "")

    def _rpc_error(
        self,
        rpc_id: Any,
        code: int,
        message: str,
        data: Any = None,
    ) -> dict[str, Any]:
        err: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        return {"jsonrpc": "2.0", "id": rpc_id, "error": err}

    def _wait_for_settle(self, task_id: str, max_seconds: float = 5 * 60) -> None:
        """Block until the task reaches a terminal or interrupted state."""
        deadline = time.time() + max_seconds
        while time.time() < deadline:
            t = self.task_store.get(task_id)
            if t is None:
                return
            s = t["status"]["state"]
            if s in TERMINAL_STATES or s in INTERRUPTED_STATES:
                return
            time.sleep(0.05)


class A2ARpcError(Exception):
    """Raised from inside dispatch helpers to surface a JSON-RPC error
    cleanly. Caught at the top-level _handle_rpc.
    """

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def _now_iso() -> str:
    from datetime import datetime, timezone

    return (
        datetime.now(tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
