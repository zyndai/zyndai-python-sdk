"""
Tests for DNS Registry Client (mocked HTTP calls).
"""

import json
import pytest
from unittest.mock import patch, MagicMock
from zyndai_agent.ed25519_identity import generate_keypair
from zyndai_agent import dns_registry


class TestRegisterAgent:
    @patch("zyndai_agent.dns_registry.requests.post")
    def test_register_success(self, mock_post):
        kp = generate_keypair()
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"agent_id": kp.agent_id}
        mock_post.return_value = mock_response

        agent_id = dns_registry.register_agent(
            registry_url="http://localhost:8080",
            keypair=kp,
            name="Test Agent",
            agent_url="http://localhost:5000",
            category="test",
            tags=["test"],
            summary="A test agent",
        )

        assert agent_id == kp.agent_id
        mock_post.assert_called_once()

        # Verify payload structure
        call_kwargs = mock_post.call_args
        body = call_kwargs[1]["json"]
        assert body["name"] == "Test Agent"
        assert body["entity_url"] == "http://localhost:5000"
        assert body["public_key"] == kp.public_key_string
        assert body["signature"].startswith("ed25519:")

    @patch("zyndai_agent.dns_registry.requests.post")
    def test_register_failure(self, mock_post):
        kp = generate_keypair()
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_post.return_value = mock_response

        with pytest.raises(RuntimeError, match="Failed to register"):
            dns_registry.register_agent(
                registry_url="http://localhost:8080",
                keypair=kp,
                name="Test",
                agent_url="http://localhost:5000",
            )


class TestGetAgent:
    @patch("zyndai_agent.dns_registry.requests.get")
    def test_get_success(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"agent_id": "agdns:abc123", "name": "Test"}
        mock_get.return_value = mock_response

        result = dns_registry.get_agent("http://localhost:8080", "agdns:abc123")
        assert result["agent_id"] == "agdns:abc123"
        mock_get.assert_called_once_with("http://localhost:8080/v1/entities/agdns:abc123")

    @patch("zyndai_agent.dns_registry.requests.get")
    def test_get_not_found(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        result = dns_registry.get_agent("http://localhost:8080", "agdns:notfound")
        assert result is None


class TestSearchAgents:
    @patch("zyndai_agent.dns_registry.requests.post")
    def test_search_with_query(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [{"agent_id": "agdns:abc", "name": "Stock Agent"}],
            "total_found": 1,
            "has_more": False,
        }
        mock_post.return_value = mock_response

        result = dns_registry.search_agents(
            registry_url="http://localhost:8080",
            query="stock analysis",
        )

        assert len(result["results"]) == 1
        assert result["total_found"] == 1

        # Verify POST body
        body = mock_post.call_args[1]["json"]
        assert body["query"] == "stock analysis"
        assert body["max_results"] == 10

    @patch("zyndai_agent.dns_registry.requests.post")
    def test_search_with_filters(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": [], "total_found": 0, "has_more": False}
        mock_post.return_value = mock_response

        dns_registry.search_agents(
            registry_url="http://localhost:8080",
            query="nlp",
            category="ai",
            tags=["nlp"],
            skills=["text-analysis"],
            max_results=5,
            enrich=True,
        )

        body = mock_post.call_args[1]["json"]
        assert body["query"] == "nlp"
        assert body["category"] == "ai"
        assert body["tags"] == ["nlp"]
        assert body["skills"] == ["text-analysis"]
        assert body["max_results"] == 5
        assert body["enrich"] is True

    @patch("zyndai_agent.dns_registry.requests.post")
    def test_search_handles_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Error"
        mock_post.return_value = mock_response

        result = dns_registry.search_agents(
            registry_url="http://localhost:8080",
            query="test",
        )
        assert result["results"] == []
        assert result["total_found"] == 0

    @patch("zyndai_agent.dns_registry.requests.post")
    def test_search_handles_network_error(self, mock_post):
        import requests
        mock_post.side_effect = requests.RequestException("Connection refused")

        result = dns_registry.search_agents(
            registry_url="http://localhost:8080",
            query="test",
        )
        assert result["results"] == []


class TestUpdateAgent:
    @patch("zyndai_agent.dns_registry.requests.put")
    def test_update_success(self, mock_put):
        kp = generate_keypair()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_put.return_value = mock_response

        result = dns_registry.update_agent(
            "http://localhost:8080",
            kp.agent_id,
            kp,
            {"name": "Updated"},
        )
        assert result is True

        # Verify auth header
        headers = mock_put.call_args[1]["headers"]
        assert "Authorization" in headers
        assert headers["Authorization"].startswith("Bearer ed25519:")


class TestDeleteAgent:
    @patch("zyndai_agent.dns_registry.requests.delete")
    def test_delete_success(self, mock_delete):
        kp = generate_keypair()
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_delete.return_value = mock_response

        result = dns_registry.delete_agent("http://localhost:8080", kp.agent_id, kp)
        assert result is True


class TestGetAgentCard:
    @patch("zyndai_agent.dns_registry.requests.get")
    def test_get_card_success(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"agent_id": "agdns:abc", "name": "Test"}
        mock_get.return_value = mock_response

        result = dns_registry.get_agent_card("http://localhost:8080", "agdns:abc")
        assert result is not None
        mock_get.assert_called_once_with("http://localhost:8080/v1/entities/agdns:abc/card")

    @patch("zyndai_agent.dns_registry.requests.get")
    def test_get_card_not_found(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        result = dns_registry.get_agent_card("http://localhost:8080", "agdns:notfound")
        assert result is None
