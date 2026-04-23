import time
import logging
import threading
import requests
from flask import Flask, request, jsonify
from pydantic import BaseModel, ValidationError
from typing import List, Callable, Optional, Dict, Any
from zyndai_agent.message import AgentMessage
from zyndai_agent.search import AgentSearchResponse
from x402 import x402ResourceServerSync
from x402.http.middleware.flask import PaymentMiddleware
from x402.http.types import RouteConfig, PaymentOption
from x402.mechanisms.evm.exact import register_exact_evm_server

# Configure logging with a more descriptive format
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("WebhookAgentCommunication")


class WebhookCommunicationManager:
    """
    HTTP Webhook-based communication manager for LangChain agents.

    This class provides tools for LangChain agents to communicate via HTTP webhooks,
    enabling multi-agent collaboration through a request-response pattern.
    Each agent runs an embedded Flask server to receive messages.
    """

    identity_credential: dict = None

    def __init__(
        self,
        entity_id: str,
        webhook_host: str = "0.0.0.0",
        webhook_port: int = 5000,
        webhook_url: Optional[str] = None,
        auto_restart: bool = True,
        message_history_limit: int = 100,
        identity_credential: dict = None,
        keypair=None,
        agent_card_builder: Optional[Callable] = None,
        price: Optional[str] = "$0.01",
        pay_to_address: Optional[str] = None,
        use_ngrok: bool = False,
        ngrok_auth_token: Optional[str] = None,
        max_file_size_bytes: Optional[int] = None,
    ):
        """
        Initialize the webhook agent communication manager.

        Args:
            entity_id: Unique identifier for this agent
            webhook_host: Host address to bind the webhook server (default: 0.0.0.0)
            webhook_port: Port number for the webhook server (default: 5000)
            webhook_url: Public webhook URL (auto-generated if None)
            auto_restart: Whether to attempt restart on failure
            message_history_limit: Maximum number of messages to keep in history
            identity_credential: DID credential for this agent (deprecated)
            keypair: Ed25519Keypair for this agent
            agent_card_builder: Callable that returns a signed Agent Card dict
        """

        self.entity_id = entity_id
        self.webhook_host = webhook_host
        self.webhook_port = webhook_port
        self.webhook_url = webhook_url
        self.auto_restart = auto_restart
        self.message_history_limit = message_history_limit
        self.use_ngrok = use_ngrok
        self.ngrok_auth_token = ngrok_auth_token
        self.ngrok_tunnel = None

        self.identity_credential = identity_credential
        self.keypair = keypair
        self.agent_card_builder = agent_card_builder

        self.is_running = False
        self.is_agent_connected = False
        self.received_messages = []
        self.message_history = []
        self.message_handlers = []
        self.target_webhook_url = None
        self.pending_responses = {}  # Store responses by message_id

        # Thread safety
        self._lock = threading.Lock()

        # Cap on /webhook body size. Bounds how big an inline base64
        # attachment can come through. Set via Flask's MAX_CONTENT_LENGTH so
        # oversized bodies are rejected before the handler reads them.
        self.max_file_size_bytes = max_file_size_bytes

        # Create Flask app
        self.flask_app = Flask(f"entity_{entity_id}")
        self.flask_app.logger.setLevel(logging.ERROR)  # Suppress Flask logging

        if max_file_size_bytes is not None:
            self.flask_app.config["MAX_CONTENT_LENGTH"] = max_file_size_bytes

        if price is not None and pay_to_address is not None:
            try:
                # x402 v2 API: create a resource server with route config
                server = x402ResourceServerSync()
                register_exact_evm_server(server)

                routes = {
                    "POST /webhook": RouteConfig(
                        accepts=PaymentOption(
                            scheme="exact",
                            network="base-sepolia",
                            pay_to=pay_to_address,
                            price=price,
                            max_timeout_seconds=300,
                        )
                    ),
                    "POST /webhook/sync": RouteConfig(
                        accepts=PaymentOption(
                            scheme="exact",
                            network="base-sepolia",
                            pay_to=pay_to_address,
                            price=price,
                            max_timeout_seconds=300,
                        )
                    ),
                }

                self.payment_middleware = PaymentMiddleware(
                    self.flask_app, routes, server, sync_facilitator_on_start=True
                )
            except Exception as e:
                logger.warning(f"Failed to initialize x402 payment middleware: {e}")
                print(f"x402 payment middleware disabled: {e}")
        else:
            print("Disabling x402, X402 payment config not provided")

        self._setup_routes()

        # Start webhook server
        self.start_webhook_server()

        # Create ngrok tunnel if requested
        if self.use_ngrok:
            self._start_ngrok_tunnel()

        print("Webhook server started")
        print(f"Listening on {self.webhook_url}")

    def _setup_routes(self):
        """Setup Flask routes for webhook endpoints."""

        @self.flask_app.route("/webhook", methods=["POST"])
        def webhook_handler():
            return self._handle_webhook_request(sync=False)

        @self.flask_app.route("/webhook/sync", methods=["POST"])
        def webhook_sync_handler():
            return self._handle_webhook_request(sync=True)

        @self.flask_app.route("/webhook/response/<message_id>", methods=["GET"])
        def webhook_response_handler(message_id: str):
            # Async callback for /webhook: the handler stores its result via
            # set_response(message_id, ...); the caller polls here to fetch it.
            # First hit returns the value and removes it; repeat calls 404.
            with self._lock:
                if message_id in self.pending_responses:
                    response = self.pending_responses.pop(message_id)
                    return jsonify({
                        "status": "success",
                        "message_id": message_id,
                        "response": response,
                        "timestamp": time.time(),
                    }), 200
            return jsonify({
                "status": "pending_or_unknown",
                "message_id": message_id,
                "error": "No response stored for this message_id (not ready, already fetched, or unknown)",
            }), 404

        @self.flask_app.route("/health", methods=["GET"])
        def health_check():
            return jsonify(
                {"status": "ok", "entity_id": self.entity_id, "timestamp": time.time()}
            ), 200

        # Agent Card route for agent-dns
        @self.flask_app.route("/.well-known/agent.json", methods=["GET"])
        def agent_card():
            if self.agent_card_builder:
                card = self.agent_card_builder()
                return jsonify(card), 200
            return jsonify({"error": "Entity Card not configured"}), 404

    def _handle_webhook_request(self, sync=False):
        """Handle incoming webhook POST requests.

        Accepts two wire formats:
        - `application/json`: inline base64 attachments (best for small files).
        - `multipart/form-data`: binary file parts + an optional JSON `payload`
          part (best for large files — no 33% base64 overhead).

        Both paths converge on a single dict that Pydantic validates against
        the agent's payload_model, so the handler sees the same shape.
        """
        try:
            content_type = (request.content_type or "").split(";", 1)[0].strip().lower()

            if content_type == "application/json":
                payload = request.get_json(silent=True) or {}
            elif content_type == "multipart/form-data":
                payload = self._parse_multipart_payload()
            else:
                logger.error(
                    f"Received request with unsupported Content-Type: {content_type!r}"
                )
                return jsonify({
                    "error": "Content-Type must be application/json or multipart/form-data",
                }), 400

            # Parse message from dict (request.get_json() returns a dict, not a string).
            # Use the developer-supplied payload model if set, else the default AgentPayload.
            payload_model = getattr(self, "payload_model", None)
            if payload_model is not None:
                message = AgentMessage.from_dict(payload, payload_model=payload_model)
            else:
                message = AgentMessage.from_dict(payload)

            logger.info(f"[{self.entity_id}] Received message from {message.sender_id}")

            # Auto-connect to sender if not connected
            if not self.is_agent_connected:
                self.is_agent_connected = True

            # Store in history
            message_with_metadata = {
                "message": message,
                "received_at": time.time(),
                "structured": True,
                "source_ip": request.remote_addr,
            }

            print("\nIncoming Message: ", message.content, "\n")

            with self._lock:
                self.received_messages.append(message_with_metadata)
                self.message_history.append(message_with_metadata)

                # Maintain history limit
                if len(self.message_history) > self.message_history_limit:
                    self.message_history = self.message_history[
                        -self.message_history_limit :
                    ]

            # Check if synchronous response is requested
            if sync:
                # Wait for handler to process and store response
                # Invoke message handlers synchronously
                for handler in self.message_handlers:
                    try:
                        handler(message, None)  # No topic in webhook context
                    except Exception as e:
                        logger.error(f"Error in message handler: {e}")

                # Wait for response (with timeout)
                timeout = 30  # 30 seconds
                start_time = time.time()
                while time.time() - start_time < timeout:
                    with self._lock:
                        if message.message_id in self.pending_responses:
                            response = self.pending_responses.pop(message.message_id)
                            return jsonify(
                                {
                                    "status": "success",
                                    "message_id": message.message_id,
                                    "response": response,
                                    "timestamp": time.time(),
                                }
                            ), 200
                    time.sleep(0.1)  # Small delay to avoid busy waiting

                # Timeout - no response received
                return jsonify(
                    {
                        "status": "timeout",
                        "message_id": message.message_id,
                        "error": "Entity did not respond within timeout period",
                        "timestamp": time.time(),
                    }
                ), 408
            else:
                # Async mode - invoke handlers and return immediately
                for handler in self.message_handlers:
                    try:
                        handler(message, None)  # No topic in webhook context
                    except Exception as e:
                        logger.error(f"Error in message handler: {e}")

                # Return success
                return jsonify(
                    {
                        "status": "received",
                        "message_id": message.message_id,
                        "timestamp": time.time(),
                    }
                ), 200

        except ValidationError as e:
            # Caller sent a payload that doesn't match the agent's declared
            # RequestPayload — missing required field, wrong type, enum
            # violation, etc. Surface Pydantic's structured error list so
            # the client can show a useful message without parsing prose.
            logger.info(f"[{self.entity_id}] rejected invalid payload: {e.error_count()} error(s)")
            return jsonify({
                "error": "validation_failed",
                "details": e.errors(include_url=False, include_context=False),
            }), 422
        except ValueError as e:
            # Bad multipart body (e.g. malformed JSON in the `payload` part).
            # Distinct from semantic validation above.
            logger.info(f"[{self.entity_id}] rejected malformed request: {e}")
            return jsonify({"error": "bad_request", "detail": str(e)}), 400
        except Exception as e:
            logger.error(f"Error handling webhook request: {e}")
            return jsonify({"error": str(e)}), 500

    def _parse_multipart_payload(self) -> dict:
        """Build the webhook payload dict from a multipart/form-data request.

        Merges two sources into a single dict for Pydantic validation:
        1. A `payload` form part containing JSON with the typed fields
           (optional — defaults to {} if absent).
        2. One or more file parts. Each file's form-field name becomes a
           key in the payload, mapped to an Attachment dict (filename,
           mime_type, size_bytes, base64-encoded data). Repeating the same
           form-field name appends to a list (useful for `pdfs: list[Attachment]`).

        File bytes are base64-encoded here so downstream validation is
        identical to the JSON path. Nothing is written to disk.
        """
        import base64 as _b64
        import json as _json

        payload_text = request.form.get("payload", "").strip()
        if payload_text:
            try:
                payload = _json.loads(payload_text)
            except _json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON in `payload` part: {e}") from e
            if not isinstance(payload, dict):
                raise ValueError("`payload` part must decode to a JSON object")
        else:
            payload = {}

        # Walk every file part; files under the same field name become a list.
        for field_name, file_storages in request.files.lists():
            field_attachments = []
            for fs in file_storages:
                if not fs or not fs.filename:
                    continue
                raw = fs.read()
                field_attachments.append({
                    "filename": fs.filename,
                    "mime_type": fs.mimetype or None,
                    "size_bytes": len(raw),
                    "data": _b64.b64encode(raw).decode("ascii"),
                })
            if not field_attachments:
                continue

            existing = payload.get(field_name)
            if isinstance(existing, list):
                existing.extend(field_attachments)
            elif existing is None:
                # Always store as list to match `list[Attachment]` typing.
                payload[field_name] = field_attachments
            else:
                # Caller declared a scalar/object under this key in JSON — we
                # don't know how to merge files into that, so surface clearly.
                raise ValueError(
                    f"Cannot merge multipart file part {field_name!r}: "
                    f"`payload` already defines it as a non-list value"
                )

        return payload

    def resolve_attachment(self, attachment, *, timeout: float = 30.0) -> bytes:
        """Return the raw bytes of an attachment, in memory, from either source.

        - `data` (base64) -> decoded inline from the JSON body
        - `url` (http/https) -> streamed download, capped at max_file_size_bytes

        Nothing is persisted to disk by the SDK; the bytes live only as long
        as the handler holds a reference to them.
        """
        if attachment.data is not None:
            return attachment.decode_data()
        if attachment.url is not None:
            return attachment.fetch_url(
                timeout=timeout, max_size_bytes=self.max_file_size_bytes
            )
        raise ValueError("Attachment has no data or url to resolve")

    def start_webhook_server(self):
        """Start Flask webhook server in background thread."""
        if self.is_running:
            logger.warning("Webhook server already running")
            return

        port = self.webhook_port

        # Probe the port before launching Flask. flask_app.run() raises inside
        # the daemon thread, where the main thread never sees it — so a busy
        # port used to be silently ignored. Fail fast here with a clear error.
        import socket
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((self.webhook_host, port))
        except OSError as e:
            probe.close()
            raise RuntimeError(
                f"Cannot start webhook server: port {port} on {self.webhook_host} "
                f"is already in use ({e}). Stop the process using it or configure "
                f"a different webhook_port."
            ) from e
        finally:
            probe.close()

        def run_flask():
            self.flask_app.run(
                host=self.webhook_host,
                port=port,
                debug=False,
                use_reloader=False,
                threaded=True,
            )

        self.flask_thread = threading.Thread(
            target=run_flask, daemon=True, name=f"WebhookServer-{self.entity_id}"
        )
        self.flask_thread.start()

        if self.webhook_url is None:
            host = (
                "localhost"
                if self.webhook_host == "0.0.0.0"
                else self.webhook_host
            )
            scheme = "https" if port == 443 else "http"
            self.webhook_url = f"{scheme}://{host}:{port}/webhook"

        self.is_running = True

        # Give Flask a moment to actually accept connections.
        time.sleep(1.5)

        logger.info(f"Webhook server started on {self.webhook_host}:{port}")

    def stop_webhook_server(self):
        """Stop the webhook server and cleanup resources."""
        if not self.is_running:
            logger.warning("Webhook server not running")
            return

        # Close ngrok tunnel if active
        if self.ngrok_tunnel is not None:
            try:
                from pyngrok import ngrok

                ngrok.disconnect(self.ngrok_tunnel.public_url)
                logger.info(f"[{self.entity_id}] Ngrok tunnel closed")
            except Exception as e:
                logger.warning(f"Failed to close ngrok tunnel: {e}")

        self.is_running = False
        logger.info(f"[{self.entity_id}] Webhook server stopped")

    def _start_ngrok_tunnel(self):
        """Create an ngrok tunnel to expose the local webhook server publicly."""
        try:
            from pyngrok import ngrok, conf
        except ImportError:
            raise ImportError(
                "pyngrok is required for ngrok tunnel support. "
                "Install it with: pip install zyndai-agent[ngrok]"
            )

        try:
            # Set auth token if provided (otherwise pyngrok uses the global config)
            if self.ngrok_auth_token:
                conf.get_default().auth_token = self.ngrok_auth_token

            # Create HTTP tunnel to the actual local port
            self.ngrok_tunnel = ngrok.connect(self.webhook_port, "http")
            public_url = self.ngrok_tunnel.public_url

            # Override the webhook URL with the ngrok public URL
            self.webhook_url = f"{public_url}/webhook"

            logger.info(
                f"[{self.entity_id}] Ngrok tunnel created: {public_url} -> localhost:{self.webhook_port}"
            )
            print(f"Ngrok tunnel active: {public_url} -> localhost:{self.webhook_port}")

        except Exception as e:
            logger.error(f"Failed to create ngrok tunnel: {e}")
            raise RuntimeError(
                f"Failed to create ngrok tunnel: {e}. "
                "Make sure ngrok is installed and your auth token is valid. "
                "Sign up at https://ngrok.com to get a free auth token."
            )

    def send_message(
        self,
        message_content: str,
        message_type: str = "query",
        receiver_id: Optional[str] = None,
    ) -> str:
        """
        Send a message to another agent via HTTP POST.

        Args:
            message_content: The main content of the message
            message_type: The type of message being sent
            receiver_id: Specific recipient ID

        Returns:
            Status message or error
        """
        if not self.is_running:
            return "Webhook server not running. Cannot send messages."

        if not self.target_webhook_url:
            return "No target entity connected. Use connect_agent() first."

        try:
            # Create structured message
            message = AgentMessage(
                content=message_content,
                sender_id=self.entity_id,
                receiver_id=receiver_id,
                message_type=message_type,
                sender_did=self.identity_credential,
                sender_public_key=self.keypair.public_key_string
                if self.keypair
                else None,
            )

            # Convert to dict for JSON serialization
            json_payload = message.to_dict()

            # Send HTTP POST request with JSON body
            response = requests.post(
                self.target_webhook_url,
                json=json_payload,
                headers={"Content-Type": "application/json"},
                timeout=30,  # 30 second timeout
            )

            # Check response
            if response.status_code == 200:
                logger.info(f"Message sent successfully to {self.target_webhook_url}")

                # Add to history
                with self._lock:
                    self.message_history.append(
                        {
                            "message": message,
                            "sent_at": time.time(),
                            "direction": "outgoing",
                            "target_url": self.target_webhook_url,
                        }
                    )

                    if len(self.message_history) > self.message_history_limit:
                        self.message_history = self.message_history[
                            -self.message_history_limit :
                        ]

                return f"Message sent successfully to topic '{self.target_webhook_url}'"
            else:
                error_msg = f"Failed to send message. HTTP {response.status_code}: {response.text}"
                logger.error(error_msg)
                return error_msg

        except requests.exceptions.Timeout:
            error_msg = "Error: Request timed out. Target entity may be offline."
            logger.error(error_msg)
            return error_msg
        except requests.exceptions.ConnectionError:
            error_msg = (
                "Error: Could not connect to target entity. Entity may be offline."
            )
            logger.error(error_msg)
            return error_msg
        except Exception as e:
            error_msg = f"Error sending message: {str(e)}"
            logger.error(f"[{self.entity_id}] {error_msg}")
            return error_msg

    def read_messages(self) -> str:
        """
        Read and clear the current message queue.

        Returns:
            Formatted string of received messages
        """
        if not self.is_running:
            return "Webhook server not running."

        if not self.received_messages:
            return "No new messages in the queue."

        # Format messages for output
        formatted_messages = []
        for item in self.received_messages:
            message = item["message"]

            formatted_msg = (
                f"From: {message.sender_id}\n"
                f"Type: {message.message_type}\n"
                f"Content: {message.content}\n"
            )
            formatted_messages.append(formatted_msg)

        # Create a combined output
        output = "Messages received:\n\n" + "\n---\n".join(formatted_messages)

        # Clear the received messages queue but keep them in history
        with self._lock:
            self.received_messages.clear()

        return output

    def add_message_handler(self, handler_function: Callable) -> None:
        """
        Add a custom message handler function.

        Args:
            handler_function: Function to call when messages arrive
                              Should accept (message, topic) parameters
        """
        with self._lock:
            self.message_handlers.append(handler_function)
        logger.info(f"[{self.entity_id}] Added custom message handler")

    def register_handler(self, handler_fn: Callable[[AgentMessage, str], None]):
        """Alias for add_message_handler for backward compatibility."""
        self.add_message_handler(handler_fn)

    def set_response(self, message_id: str, response):
        """Set the sync response for a webhook request.

        `response` may be:
          - a str: sent as-is (legacy behavior, no validation)
          - a dict: serialized to JSON; validated against `output_model`
            if the agent declared one
          - a Pydantic BaseModel: serialized via model_dump_json; coerced
            through `output_model` if declared and the instance is a
            different class

        Validation failures are converted to an error response (so the
        caller gets a clean 500 with details) rather than raised — the
        webhook handler is still waiting on this slot.
        """
        import json as _json
        output_model = getattr(self, "output_model", None)

        if isinstance(response, str):
            final = response
        elif isinstance(response, BaseModel):
            if output_model is not None and not isinstance(response, output_model):
                try:
                    response = output_model.model_validate(response.model_dump())
                except ValidationError as e:
                    logger.error(
                        f"[{self.entity_id}] handler output failed {output_model.__name__} validation"
                    )
                    final = _json.dumps({
                        "error": "handler_output_invalid",
                        "details": e.errors(include_url=False, include_context=False),
                    })
                    with self._lock:
                        self.pending_responses[message_id] = final
                    return
            final = response.model_dump_json()
        elif isinstance(response, dict):
            if output_model is not None:
                try:
                    validated = output_model.model_validate(response)
                    final = validated.model_dump_json()
                except ValidationError as e:
                    logger.error(
                        f"[{self.entity_id}] handler output failed {output_model.__name__} validation"
                    )
                    final = _json.dumps({
                        "error": "handler_output_invalid",
                        "details": e.errors(include_url=False, include_context=False),
                    })
            else:
                final = _json.dumps(response)
        else:
            # Anything else — stringify (rare; keeps the door open).
            final = str(response)

        with self._lock:
            self.pending_responses[message_id] = final
        logger.info(f"[{self.entity_id}] Set response for message {message_id}")

    def get_connection_status(self) -> Dict[str, Any]:
        """Get the current webhook server status and statistics."""
        return {
            "entity_id": self.entity_id,
            "is_running": self.is_running,
            "webhook_url": self.webhook_url,
            "webhook_port": self.webhook_port,
            "target_webhook_url": self.target_webhook_url,
            "pending_messages": len(self.received_messages),
            "message_history_count": len(self.message_history),
        }

    def get_message_history(
        self, limit: int = None, filter_by_topic: str = None
    ) -> List[Dict[str, Any]]:
        """Get the message history with optional filtering."""
        with self._lock:
            history = self.message_history.copy()

        # Apply limit if specified
        if limit is not None:
            history = history[-limit:]

        return history

    def connect_agent(self, agent: AgentSearchResponse):
        """
        Connect to another agent.

        Supports both new agent-dns format (entity_url + Agent Card) and
        legacy format (httpWebhookUrl).

        Args:
            agent: Agent search response dict
        """
        # New agent-dns format: use entity_url to fetch Agent Card
        entity_url = agent.get("entity_url")
        if entity_url:
            # Try to fetch Agent Card to get invoke endpoint
            try:
                card_url = f"{entity_url.rstrip('/')}/.well-known/agent.json"
                resp = requests.get(card_url, timeout=10)
                if resp.status_code == 200:
                    card = resp.json()
                    endpoints = card.get("endpoints", {})
                    invoke_url = endpoints.get("invoke")
                    if invoke_url:
                        self.target_webhook_url = invoke_url
                        self.is_agent_connected = True
                        logger.info(
                            f"Connected to entity {agent.get('entity_id', agent.get('name', 'unknown'))} "
                            f"via Card at {self.target_webhook_url}"
                        )
                        return
            except Exception as e:
                logger.warning(f"Could not fetch Agent Card from {entity_url}: {e}")

            # Fallback: use entity_url/webhook/sync directly
            self.target_webhook_url = f"{entity_url.rstrip('/')}/webhook/sync"
            self.is_agent_connected = True
            logger.info(
                f"Connected to entity at {self.target_webhook_url} (direct, no card)"
            )
            return

        # Legacy format: httpWebhookUrl or webhookUrl
        webhook_url = agent.get("httpWebhookUrl") or agent.get("webhookUrl")
        if webhook_url:
            self.target_webhook_url = webhook_url
            self.is_agent_connected = True
            logger.info(
                f"Connected to agent {agent.get('didIdentifier', agent.get('entity_id', 'unknown'))} at {self.target_webhook_url}"
            )
            return

        raise ValueError(
            "Agent does not have entity_url or httpWebhookUrl. Cannot connect via webhook."
        )
