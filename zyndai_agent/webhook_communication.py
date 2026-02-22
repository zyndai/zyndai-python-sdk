import time
import logging
import threading
import requests
from flask import Flask, request, jsonify
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
        agent_id: str,
        webhook_host: str = "0.0.0.0",
        webhook_port: int = 5000,
        webhook_url: Optional[str] = None,
        auto_restart: bool = True,
        message_history_limit: int = 100,
        identity_credential: dict = None,
        price: Optional[str] = "$0.01",
        pay_to_address: Optional[str] = None,
        use_ngrok: bool = False,
        ngrok_auth_token: Optional[str] = None,
    ):
        """
        Initialize the webhook agent communication manager.

        Args:
            agent_id: Unique identifier for this agent
            webhook_host: Host address to bind the webhook server (default: 0.0.0.0)
            webhook_port: Port number for the webhook server (default: 5000)
            webhook_url: Public webhook URL (auto-generated if None)
            auto_restart: Whether to attempt restart on failure
            message_history_limit: Maximum number of messages to keep in history
            identity_credential: DID credential for this agent
        """

        self.agent_id = agent_id
        self.webhook_host = webhook_host
        self.webhook_port = webhook_port
        self.webhook_url = webhook_url
        self.auto_restart = auto_restart
        self.message_history_limit = message_history_limit
        self.use_ngrok = use_ngrok
        self.ngrok_auth_token = ngrok_auth_token
        self.ngrok_tunnel = None

        self.identity_credential = identity_credential

        self.is_running = False
        self.is_agent_connected = False
        self.received_messages = []
        self.message_history = []
        self.message_handlers = []
        self.target_webhook_url = None
        self.pending_responses = {}  # Store responses by message_id

        # Thread safety
        self._lock = threading.Lock()

        # Create Flask app
        self.flask_app = Flask(f"agent_{agent_id}")
        self.flask_app.logger.setLevel(logging.ERROR)  # Suppress Flask logging

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

        print("Agent webhook server started")
        print(f"Listening on {self.webhook_url}")

    def _setup_routes(self):
        """Setup Flask routes for webhook endpoints."""

        @self.flask_app.route("/webhook", methods=["POST"])
        def webhook_handler():
            return self._handle_webhook_request(sync=False)

        @self.flask_app.route("/webhook/sync", methods=["POST"])
        def webhook_sync_handler():
            return self._handle_webhook_request(sync=True)

        @self.flask_app.route("/health", methods=["GET"])
        def health_check():
            return jsonify(
                {"status": "ok", "agent_id": self.agent_id, "timestamp": time.time()}
            ), 200

    def _handle_webhook_request(self, sync=False):
        """Handle incoming webhook POST requests."""
        try:
            # Verify request is JSON
            if not request.is_json:
                logger.error("Received non-JSON request")
                return jsonify({"error": "Content-Type must be application/json"}), 400

            payload = request.get_json()

            # Parse message from dict (request.get_json() returns a dict, not a string)
            message = AgentMessage.from_dict(payload)

            logger.info(f"[{self.agent_id}] Received message from {message.sender_id}")

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
                        "error": "Agent did not respond within timeout period",
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

        except Exception as e:
            logger.error(f"Error handling webhook request: {e}")
            return jsonify({"error": str(e)}), 500

    def start_webhook_server(self):
        """Start Flask webhook server in background thread."""
        if self.is_running:
            logger.warning("Webhook server already running")
            return

        # Try to bind to configured port, retry with different ports if needed
        max_retries = 10
        server_started = False

        for attempt in range(max_retries):
            try:
                port = self.webhook_port + attempt

                def run_flask():
                    self.flask_app.run(
                        host=self.webhook_host,
                        port=port,
                        debug=False,
                        use_reloader=False,
                        threaded=True,
                    )

                self.flask_thread = threading.Thread(
                    target=run_flask, daemon=True, name=f"WebhookServer-{self.agent_id}"
                )
                self.flask_thread.start()

                # Update actual port used
                self.webhook_port = port

                # Auto-form webhook URL from host and port
                if self.webhook_url is None:
                    host = (
                        "localhost"
                        if self.webhook_host == "0.0.0.0"
                        else self.webhook_host
                    )
                    scheme = "https" if port == 443 else "http"
                    self.webhook_url = f"{scheme}://{host}:{port}/webhook"

                self.is_running = True
                server_started = True

                # Wait for server to start
                time.sleep(1.5)

                logger.info(f"Webhook server started on {self.webhook_host}:{port}")
                break

            except OSError as e:
                if "Address already in use" in str(e) and attempt < max_retries - 1:
                    logger.warning(f"Port {port} already in use, trying next port...")
                    continue
                else:
                    logger.error(f"Failed to start webhook server: {e}")
                    raise

        if not server_started:
            raise RuntimeError("Failed to start webhook server after multiple attempts")

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
                logger.info(f"[{self.agent_id}] Ngrok tunnel closed")
            except Exception as e:
                logger.warning(f"Failed to close ngrok tunnel: {e}")

        self.is_running = False
        logger.info(f"[{self.agent_id}] Webhook server stopped")

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
                f"[{self.agent_id}] Ngrok tunnel created: {public_url} -> localhost:{self.webhook_port}"
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
            return "No target agent connected. Use connect_agent() first."

        try:
            # Create structured message
            message = AgentMessage(
                content=message_content,
                sender_id=self.agent_id,
                receiver_id=receiver_id,
                message_type=message_type,
                sender_did=self.identity_credential,
            )

            # Convert to dict for JSON serialization
            # Note: use to_dict() not to_json() - json= parameter expects a dict
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
            error_msg = "Error: Request timed out. Target agent may be offline."
            logger.error(error_msg)
            return error_msg
        except requests.exceptions.ConnectionError:
            error_msg = (
                "Error: Could not connect to target agent. Agent may be offline."
            )
            logger.error(error_msg)
            return error_msg
        except Exception as e:
            error_msg = f"Error sending message: {str(e)}"
            logger.error(f"[{self.agent_id}] {error_msg}")
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
        logger.info(f"[{self.agent_id}] Added custom message handler")

    def register_handler(self, handler_fn: Callable[[AgentMessage, str], None]):
        """Alias for add_message_handler for backward compatibility."""
        self.add_message_handler(handler_fn)

    def set_response(self, message_id: str, response: str):
        """
        Set a response for a specific message ID (for synchronous responses).

        Args:
            message_id: The ID of the message being responded to
            response: The response content
        """
        with self._lock:
            self.pending_responses[message_id] = response
        logger.info(f"[{self.agent_id}] Set response for message {message_id}")

    def get_connection_status(self) -> Dict[str, Any]:
        """
        Get the current webhook server status and statistics.

        Returns:
            Dictionary with connection information
        """
        return {
            "agent_id": self.agent_id,
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
        """
        Get the message history with optional filtering.

        Args:
            limit: Maximum number of messages to return (None for all)
            filter_by_topic: Only return messages from this topic (not applicable for webhooks)

        Returns:
            List of message history entries
        """
        with self._lock:
            history = self.message_history.copy()

        # Apply limit if specified
        if limit is not None:
            history = history[-limit:]

        return history

    def connect_agent(self, agent: AgentSearchResponse):
        """
        Connect to another agent using their webhook URL.

        Args:
            agent: Agent search response containing httpWebhookUrl
        """
        # Support both old 'webhookUrl' and new 'httpWebhookUrl' field names
        webhook_url = agent.get("httpWebhookUrl") or agent.get("webhookUrl")
        if not webhook_url:
            raise ValueError(
                "Agent does not have httpWebhookUrl. Cannot connect via webhook."
            )

        self.target_webhook_url = webhook_url
        self.is_agent_connected = True

        logger.info(
            f"Connected to agent {agent.get('didIdentifier', 'unknown')} at {self.target_webhook_url}"
        )
