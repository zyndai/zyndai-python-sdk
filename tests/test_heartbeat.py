"""
Tests for the heartbeat system: message format, WS URL construction, and graceful shutdown.
"""

import json
import threading
import time
from unittest.mock import MagicMock, patch, call

from zyndai_agent.ed25519_identity import generate_keypair, verify


class TestHeartbeatMessageFormat:
    """Test that heartbeat messages contain a valid timestamp and Ed25519 signature."""

    def test_heartbeat_payload_structure(self):
        """Simulate what _heartbeat_loop builds and verify shape."""
        from zyndai_agent.ed25519_identity import sign as ed25519_sign

        kp = generate_keypair()
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        signature = ed25519_sign(kp.private_key, ts.encode())
        payload = json.dumps({"timestamp": ts, "signature": signature})

        parsed = json.loads(payload)
        assert "timestamp" in parsed
        assert "signature" in parsed
        # Verify signature is valid
        assert verify(kp.public_key_b64, ts.encode(), parsed["signature"])

    def test_timestamp_is_utc_iso(self):
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        assert ts.endswith("Z")
        assert "T" in ts


class TestWebSocketURLConstruction:
    """Test http→ws and https→wss URL conversion used in _start_heartbeat."""

    def test_http_to_ws(self):
        url = "http://localhost:8080"
        ws_url = url.replace("https://", "wss://").replace("http://", "ws://")
        assert ws_url == "ws://localhost:8080"

    def test_https_to_wss(self):
        url = "https://registry.example.com"
        ws_url = url.replace("https://", "wss://").replace("http://", "ws://")
        assert ws_url == "wss://registry.example.com"

    def test_full_ws_endpoint(self):
        url = "http://localhost:8080"
        agent_id = "agdns:test-agent-123"
        ws_url = url.replace("https://", "wss://").replace("http://", "ws://")
        ws_url = f"{ws_url}/v1/entities/{agent_id}/ws"
        assert ws_url == "ws://localhost:8080/v1/entities/agdns:test-agent-123/ws"


class TestStopHeartbeat:
    """Test that stop_heartbeat signals the thread and joins it."""

    def test_stop_sets_event_and_joins(self):
        """Create a minimal mock to verify stop_heartbeat behavior."""
        from zyndai_agent.agent import ZyndAIAgent

        # Build a minimal agent without invoking __init__
        agent = object.__new__(ZyndAIAgent)
        agent._heartbeat_stop = threading.Event()

        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        agent._heartbeat_thread = mock_thread

        agent.stop_heartbeat()

        assert agent._heartbeat_stop.is_set()
        mock_thread.join.assert_called_once_with(timeout=5)

    def test_stop_noop_when_no_thread(self):
        from zyndai_agent.agent import ZyndAIAgent

        agent = object.__new__(ZyndAIAgent)
        agent._heartbeat_stop = threading.Event()
        agent._heartbeat_thread = None

        # Should not raise
        agent.stop_heartbeat()
        assert not agent._heartbeat_stop.is_set()


class TestHeartbeatWebSocketIntegration:
    """Test _start_heartbeat with a mocked websockets client."""

    @patch("websockets.sync.client.connect")
    def test_sends_heartbeat_and_stops(self, mock_connect):
        from zyndai_agent.agent import ZyndAIAgent

        # Set up mock WebSocket
        mock_ws = MagicMock()
        mock_connect.return_value.__enter__ = MagicMock(return_value=mock_ws)
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)

        # Build minimal agent
        agent = object.__new__(ZyndAIAgent)
        kp = generate_keypair()
        agent.keypair = kp
        agent.agent_id = "agdns:heartbeat-test"
        agent._heartbeat_stop = threading.Event()
        agent._heartbeat_thread = None

        # Start heartbeat, let it send one message, then stop
        agent._start_heartbeat("http://localhost:8080")

        # Give the thread a moment to connect and send
        time.sleep(1.5)
        agent.stop_heartbeat()

        # Verify websocket was opened with correct URL
        mock_connect.assert_called_with("ws://localhost:8080/v1/entities/agdns:heartbeat-test/ws")

        # Verify at least one message was sent
        assert mock_ws.send.call_count >= 1
        sent = json.loads(mock_ws.send.call_args_list[0][0][0])
        assert "timestamp" in sent
        assert "signature" in sent
