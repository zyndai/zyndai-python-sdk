import time
import json
import logging
import threading
import requests
from flask import Flask, request, jsonify
from typing import List, Callable, Optional, Dict, Any
from zyndai_agent.message import AgentMessage
from zyndai_agent.utils import encrypt_message, decrypt_message
from zyndai_agent.search import AgentSearchResponse

# Configure logging with a more descriptive format
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
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
    identity_credential_connected_agent: dict = None
    secret_seed = None

    def __init__(
        self,
        agent_id: str,
        webhook_host: str = "0.0.0.0",
        webhook_port: int = 5000,
        webhook_url: Optional[str] = None,
        auto_restart: bool = True,
        message_history_limit: int = 100,
        identity_credential: dict = None,
        secret_seed: str = None
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
            secret_seed: Secret seed for encryption/decryption
        """

        self.agent_id = agent_id
        self.webhook_host = webhook_host
        self.webhook_port = webhook_port
        self.webhook_url = webhook_url
        self.auto_restart = auto_restart
        self.message_history_limit = message_history_limit

        self.identity_credential = identity_credential
        self.secret_seed = secret_seed

        self.is_running = False
        self.is_agent_connected = False
        self.received_messages = []
        self.message_history = []
        self.message_handlers = []
        self.target_webhook_url = None

        # Thread safety
        self._lock = threading.Lock()

        # Create Flask app
        self.flask_app = Flask(f"agent_{agent_id}")
        self.flask_app.logger.setLevel(logging.ERROR)  # Suppress Flask logging
        self._setup_routes()

        # Start webhook server
        self.start_webhook_server()

        print("Agent webhook server started")
        print(f"Listening on {self.webhook_url}")

    def _setup_routes(self):
        """Setup Flask routes for webhook endpoints."""

        @self.flask_app.route('/webhook', methods=['POST'])
        def webhook_handler():
            return self._handle_webhook_request()

        @self.flask_app.route('/health', methods=['GET'])
        def health_check():
            return jsonify({
                "status": "ok",
                "agent_id": self.agent_id,
                "timestamp": time.time()
            }), 200

    def _handle_webhook_request(self):
        """Handle incoming webhook POST requests."""
        try:
            # Verify request is JSON
            if not request.is_json:
                logger.error("Received non-JSON request")
                return jsonify({"error": "Content-Type must be application/json"}), 400

            encrypted_payload = request.get_json()

            # Decrypt message
            decrypted_payload = decrypt_message(
                encrypted_payload,
                self.secret_seed,
                self.identity_credential
            )

            # Parse message
            message = AgentMessage.from_json(decrypted_payload)

            logger.info(f"[{self.agent_id}] Received message from {message.sender_id}")

            # Auto-connect to sender if not connected
            if not self.is_agent_connected:
                self.identity_credential_connected_agent = message.sender_did
                self.is_agent_connected = True

            # Store in history
            message_with_metadata = {
                "message": message,
                "received_at": time.time(),
                "structured": True,
                "source_ip": request.remote_addr
            }

            print("\nIncoming Message: ", message.content, "\n")

            with self._lock:
                self.received_messages.append(message_with_metadata)
                self.message_history.append(message_with_metadata)

                # Maintain history limit
                if len(self.message_history) > self.message_history_limit:
                    self.message_history = self.message_history[-self.message_history_limit:]

            # Invoke message handlers
            for handler in self.message_handlers:
                try:
                    handler(message, None)  # No topic in webhook context
                except Exception as e:
                    logger.error(f"Error in message handler: {e}")

            # Return success
            return jsonify({
                "status": "received",
                "message_id": message.message_id,
                "timestamp": time.time()
            }), 200

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
                        threaded=True
                    )

                self.flask_thread = threading.Thread(
                    target=run_flask,
                    daemon=True,
                    name=f"WebhookServer-{self.agent_id}"
                )
                self.flask_thread.start()

                # Update actual port used
                self.webhook_port = port

                # Update webhook URL if not manually configured
                if self.webhook_url is None:
                    # Use localhost for local development, can be overridden
                    self.webhook_url = f"http://localhost:{port}/webhook"

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

        self.is_running = False
        logger.info(f"[{self.agent_id}] Webhook server stopped")

    def send_message(
        self,
        message_content: str,
        message_type: str = "query",
        receiver_id: Optional[str] = None
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
                sender_did=self.identity_credential
            )

            # Convert to JSON and encrypt
            json_payload = message.to_json()
            encrypted_message = encrypt_message(
                json_payload,
                self.identity_credential_connected_agent
            )

            # Send HTTP POST request
            response = requests.post(
                self.target_webhook_url,
                json=encrypted_message,
                headers={"Content-Type": "application/json"},
                timeout=30  # 30 second timeout
            )

            # Check response
            if response.status_code == 200:
                logger.info(f"Message sent successfully to {self.target_webhook_url}")

                # Add to history
                with self._lock:
                    self.message_history.append({
                        "message": message,
                        "sent_at": time.time(),
                        "direction": "outgoing",
                        "target_url": self.target_webhook_url
                    })

                    if len(self.message_history) > self.message_history_limit:
                        self.message_history = self.message_history[-self.message_history_limit:]

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
            error_msg = "Error: Could not connect to target agent. Agent may be offline."
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
            "message_history_count": len(self.message_history)
        }

    def get_message_history(
        self,
        limit: int = None,
        filter_by_topic: str = None
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
            agent: Agent search response containing webhookUrl and did
        """
        if "webhookUrl" not in agent:
            raise ValueError("Agent does not have webhookUrl. Cannot connect via webhook.")

        self.target_webhook_url = agent["webhookUrl"]
        self.identity_credential_connected_agent = json.loads(agent["did"])
        self.is_agent_connected = True

        logger.info(f"Connected to agent {agent['didIdentifier']} at {self.target_webhook_url}")
