"""
Tests for SearchAndDiscoveryManager with agent-dns POST-based search.
"""

import pytest
from unittest.mock import patch, MagicMock
from zyndai_agent.search import SearchAndDiscoveryManager, AgentSearchResponse


class TestSearchAndDiscoveryInit:
    def test_default_registry_url(self):
        mgr = SearchAndDiscoveryManager()
        assert mgr.registry_url == "http://localhost:8080"
        assert mgr.agents == []

    def test_custom_registry_url(self):
        mgr = SearchAndDiscoveryManager(registry_url="https://registry.zynd.ai")
        assert mgr.registry_url == "https://registry.zynd.ai"


class TestSearchAgents:
    @patch("zyndai_agent.dns_registry.requests.post")
    def test_search_with_keyword(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {"agent_id": "agdns:abc", "name": "StockAgent", "summary": "Analyzes stocks"},
            ],
            "total_found": 1,
            "has_more": False,
        }
        mock_post.return_value = mock_response

        mgr = SearchAndDiscoveryManager()
        results = mgr.search_agents(keyword="stock analysis")

        assert len(results) == 1
        assert results[0]["name"] == "StockAgent"
        mock_post.assert_called_once()
        # Verify query was passed in POST body
        body = mock_post.call_args[1]["json"]
        assert body["query"] == "stock analysis"

    @patch("zyndai_agent.dns_registry.requests.post")
    def test_search_empty_results(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": [], "total_found": 0, "has_more": False}
        mock_post.return_value = mock_response

        mgr = SearchAndDiscoveryManager()
        results = mgr.search_agents(keyword="nonexistent")
        assert results == []

    @patch("zyndai_agent.dns_registry.requests.post")
    def test_search_with_all_filters(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": [], "total_found": 0, "has_more": False}
        mock_post.return_value = mock_response

        mgr = SearchAndDiscoveryManager()
        mgr.search_agents(
            keyword="nlp",
            category="ai",
            tags=["nlp"],
            skills=["text-analysis"],
            protocols=["http"],
            limit=5,
            enrich=True,
        )

        body = mock_post.call_args[1]["json"]
        assert body["query"] == "nlp"
        assert body["category"] == "ai"
        assert body["tags"] == ["nlp"]
        assert body["skills"] == ["text-analysis"]
        assert body["protocols"] == ["http"]
        assert body["max_results"] == 5
        assert body["enrich"] is True

    @patch("zyndai_agent.dns_registry.requests.post")
    def test_search_handles_api_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Error"
        mock_post.return_value = mock_response

        mgr = SearchAndDiscoveryManager()
        results = mgr.search_agents(keyword="test")
        assert results == []

    @patch("zyndai_agent.dns_registry.requests.post")
    def test_search_handles_network_error(self, mock_post):
        import requests

        mock_post.side_effect = requests.RequestException("Connection refused")

        mgr = SearchAndDiscoveryManager()
        results = mgr.search_agents(keyword="test")
        assert results == []


class TestSearchByCapabilities:
    @patch("zyndai_agent.dns_registry.requests.post")
    def test_capabilities_converted_to_query_and_skills(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": [], "total_found": 0, "has_more": False}
        mock_post.return_value = mock_response

        mgr = SearchAndDiscoveryManager()
        mgr.search_agents_by_capabilities(capabilities=["nlp", "search"])

        body = mock_post.call_args[1]["json"]
        assert body["query"] == "nlp search"
        assert body["skills"] == ["nlp", "search"]

    @patch("zyndai_agent.dns_registry.requests.post")
    def test_empty_capabilities(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": [], "total_found": 0, "has_more": False}
        mock_post.return_value = mock_response

        mgr = SearchAndDiscoveryManager()
        mgr.search_agents_by_capabilities(capabilities=[])
        body = mock_post.call_args[1]["json"]
        # query should not be set (None filtered out by dns_registry)
        assert "query" not in body or body.get("query") is None

    @patch("zyndai_agent.dns_registry.requests.post")
    def test_top_k_sets_limit(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": [], "total_found": 0, "has_more": False}
        mock_post.return_value = mock_response

        mgr = SearchAndDiscoveryManager()
        mgr.search_agents_by_capabilities(capabilities=["nlp"], top_k=3)

        body = mock_post.call_args[1]["json"]
        assert body["max_results"] == 3


class TestSearchByKeyword:
    @patch("zyndai_agent.dns_registry.requests.post")
    def test_search_by_keyword(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [{"agent_id": "agdns:1"}],
            "total_found": 1,
            "has_more": False,
        }
        mock_post.return_value = mock_response

        mgr = SearchAndDiscoveryManager()
        results = mgr.search_agents_by_keyword("stock data", limit=5)

        assert len(results) == 1
        body = mock_post.call_args[1]["json"]
        assert body["query"] == "stock data"
        assert body["max_results"] == 5


class TestGetAgentById:
    @patch("zyndai_agent.dns_registry.requests.get")
    def test_get_agent_success(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"agent_id": "agdns:abc", "name": "Agent"}
        mock_get.return_value = mock_response

        mgr = SearchAndDiscoveryManager()
        result = mgr.get_agent_by_id("agdns:abc")
        assert result["agent_id"] == "agdns:abc"
        mock_get.assert_called_once_with("http://localhost:8080/v1/entities/agdns:abc")

    @patch("zyndai_agent.dns_registry.requests.get")
    def test_get_agent_not_found(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        mgr = SearchAndDiscoveryManager()
        result = mgr.get_agent_by_id("nonexistent")
        assert result is None

    @patch("zyndai_agent.dns_registry.requests.get")
    def test_get_agent_network_error(self, mock_get):
        import requests

        mock_get.side_effect = requests.RequestException("timeout")

        mgr = SearchAndDiscoveryManager()
        result = mgr.get_agent_by_id("agdns:abc")
        assert result is None


class TestGetAgentCard:
    @patch("zyndai_agent.dns_registry.requests.get")
    def test_get_card(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"agent_id": "agdns:abc", "name": "Test"}
        mock_get.return_value = mock_response

        mgr = SearchAndDiscoveryManager()
        result = mgr.get_agent_card("agdns:abc")
        assert result is not None
