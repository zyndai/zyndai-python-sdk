"""
Tests for WebhookCommunicationManager: connect_agent, send_message, read_messages, etc.
Uses the Flask test client to avoid starting real servers.
"""

import json
import time
import pytest
from unittest.mock import MagicMock, patch
from zyndai_agent.webhook_communication import WebhookCommunicationManager
from zyndai_agent.message import AgentMessage


# ---------------------------------------------------------------------------
# Fixture: create a manager without actually starting the Flask server
# ---------------------------------------------------------------------------


@pytest.fixture
def manager():
    """Create a WebhookCommunicationManager with the server start mocked out."""
    with patch.object(WebhookCommunicationManager, "start_webhook_server"):
        mgr = WebhookCommunicationManager(
            agent_id="test-agent",
            webhook_host="0.0.0.0",
            webhook_port=15000,
            webhook_url="http://localhost:15000/webhook",
            identity_credential={"issuer": "did:test"},
            price=None,
            pay_to_address=None,
        )
        mgr.is_running = True
        return mgr


# ---------------------------------------------------------------------------
# Flask test client fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def client(manager):
    """Flask test client for the manager's embedded server."""
    manager.flask_app.config["TESTING"] = True
    return manager.flask_app.test_client()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWebhookRoutes:
    def test_health_check(self, client, manager):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["agent_id"] == "test-agent"

    def test_webhook_rejects_non_json(self, client):
        resp = client.post("/webhook", data="not json", content_type="text/plain")
        assert resp.status_code == 400

    def test_webhook_async_receives_message(self, client, manager):
        payload = {
            "content": "hello",
            "sender_id": "sender-agent",
            "message_type": "query",
        }
        resp = client.post("/webhook", json=payload)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "received"

        # Message should be in received_messages
        assert len(manager.received_messages) == 1
        assert manager.received_messages[0]["message"].content == "hello"

    def test_webhook_sync_with_handler(self, client, manager):
        """Sync webhook should invoke handler and wait for response."""

        def handler(message, topic):
            manager.set_response(message.message_id, "sync response")

        manager.add_message_handler(handler)

        payload = {
            "content": "sync question",
            "sender_id": "sender",
            "message_type": "query",
            "message_id": "msg-sync-1",
        }
        resp = client.post("/webhook/sync", json=payload)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "success"
        assert data["response"] == "sync response"


class TestConnectAgent:
    def test_connect_with_http_webhook_url(self, manager):
        agent = {
            "httpWebhookUrl": "http://remote:5000/webhook",
            "didIdentifier": "did:remote",
        }
        manager.connect_agent(agent)
        assert manager.target_webhook_url == "http://remote:5000/webhook"
        assert manager.is_agent_connected is True

    def test_connect_with_legacy_webhook_url(self, manager):
        agent = {"webhookUrl": "http://legacy:5000/webhook"}
        manager.connect_agent(agent)
        assert manager.target_webhook_url == "http://legacy:5000/webhook"

    def test_connect_raises_without_url(self, manager):
        with pytest.raises(ValueError, match="does not have httpWebhookUrl"):
            manager.connect_agent({"name": "No URL Agent"})


class TestSendMessage:
    @patch("zyndai_agent.webhook_communication.requests.post")
    def test_send_success(self, mock_post, manager):
        mock_post.return_value = MagicMock(status_code=200, text="OK")
        manager.target_webhook_url = "http://remote:5000/webhook"

        result = manager.send_message("hello remote agent")
        assert "sent successfully" in result
        mock_post.assert_called_once()

        # Verify JSON payload structure
        call_kwargs = mock_post.call_args
        payload = call_kwargs[1]["json"]
        assert payload["content"] == "hello remote agent"
        assert payload["sender_id"] == "test-agent"

    def test_send_fails_when_not_running(self, manager):
        manager.is_running = False
        result = manager.send_message("test")
        assert "not running" in result

    def test_send_fails_when_no_target(self, manager):
        manager.target_webhook_url = None
        result = manager.send_message("test")
        assert "No target agent" in result

    @patch("zyndai_agent.webhook_communication.requests.post")
    def test_send_handles_http_error(self, mock_post, manager):
        mock_post.return_value = MagicMock(status_code=500, text="Server Error")
        manager.target_webhook_url = "http://remote:5000/webhook"

        result = manager.send_message("test")
        assert "Failed" in result

    @patch("zyndai_agent.webhook_communication.requests.post")
    def test_send_handles_timeout(self, mock_post, manager):
        import requests

        mock_post.side_effect = requests.exceptions.Timeout()
        manager.target_webhook_url = "http://remote:5000/webhook"

        result = manager.send_message("test")
        assert "timed out" in result

    @patch("zyndai_agent.webhook_communication.requests.post")
    def test_send_handles_connection_error(self, mock_post, manager):
        import requests

        mock_post.side_effect = requests.exceptions.ConnectionError()
        manager.target_webhook_url = "http://remote:5000/webhook"

        result = manager.send_message("test")
        assert "Could not connect" in result


class TestReadMessages:
    def test_read_empty_queue(self, manager):
        result = manager.read_messages()
        assert "No new messages" in result

    def test_read_clears_queue(self, client, manager):
        # Send a message first
        client.post("/webhook", json={"content": "msg1", "sender_id": "s1"})
        client.post("/webhook", json={"content": "msg2", "sender_id": "s2"})

        assert len(manager.received_messages) == 2

        result = manager.read_messages()
        assert "msg1" in result
        assert "msg2" in result
        assert len(manager.received_messages) == 0  # Queue cleared

    def test_read_when_not_running(self, manager):
        manager.is_running = False
        result = manager.read_messages()
        assert "not running" in result


class TestMessageHandler:
    def test_add_handler(self, manager):
        handler = MagicMock()
        manager.add_message_handler(handler)
        assert handler in manager.message_handlers

    def test_register_handler_alias(self, manager):
        handler = MagicMock()
        manager.register_handler(handler)
        assert handler in manager.message_handlers

    def test_handler_called_on_async_webhook(self, client, manager):
        handler = MagicMock()
        manager.add_message_handler(handler)

        client.post("/webhook", json={"content": "trigger", "sender_id": "s1"})
        handler.assert_called_once()
        msg_arg = handler.call_args[0][0]
        assert isinstance(msg_arg, AgentMessage)
        assert msg_arg.content == "trigger"


class TestConnectionStatus:
    def test_get_connection_status(self, manager):
        status = manager.get_connection_status()
        assert status["agent_id"] == "test-agent"
        assert status["is_running"] is True
        assert status["webhook_url"] == "http://localhost:15000/webhook"
        assert status["webhook_port"] == 15000


class TestMessageHistory:
    def test_history_limit(self, client, manager):
        manager.message_history_limit = 3
        for i in range(5):
            client.post("/webhook", json={"content": f"msg-{i}", "sender_id": "s"})

        # Should only keep last 3
        assert len(manager.message_history) == 3

    def test_get_message_history_with_limit(self, client, manager):
        for i in range(5):
            client.post("/webhook", json={"content": f"msg-{i}", "sender_id": "s"})

        history = manager.get_message_history(limit=2)
        assert len(history) == 2

    def test_set_response(self, manager):
        manager.set_response("msg-1", "response text")
        assert manager.pending_responses["msg-1"] == "response text"
