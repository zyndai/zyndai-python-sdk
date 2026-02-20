"""
Tests for SearchAndDiscoveryManager.
"""

import pytest
from unittest.mock import patch, MagicMock
from zyndai_agent.search import SearchAndDiscoveryManager, AgentSearchResponse


class TestSearchAndDiscoveryInit:
    def test_default_registry_url(self):
        mgr = SearchAndDiscoveryManager()
        assert mgr.registry_url == "http://localhost:3002"
        assert mgr.agents == []

    def test_custom_registry_url(self):
        mgr = SearchAndDiscoveryManager(registry_url="https://registry.zynd.ai")
        assert mgr.registry_url == "https://registry.zynd.ai"


class TestSearchAgents:
    @patch("zyndai_agent.search.requests.get")
    def test_search_with_keyword(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {"id": "1", "name": "StockAgent", "description": "Analyzes stocks"},
            ],
            "count": 1,
            "total": 1,
        }
        mock_get.return_value = mock_response

        mgr = SearchAndDiscoveryManager()
        results = mgr.search_agents(keyword="stock analysis")

        assert len(results) == 1
        assert results[0]["name"] == "StockAgent"
        mock_get.assert_called_once()
        # Verify keyword was passed in params
        call_kwargs = mock_get.call_args
        assert call_kwargs[1]["params"]["keyword"] == "stock analysis"

    @patch("zyndai_agent.search.requests.get")
    def test_search_empty_results(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": [], "count": 0, "total": 0}
        mock_get.return_value = mock_response

        mgr = SearchAndDiscoveryManager()
        results = mgr.search_agents(keyword="nonexistent")
        assert results == []

    @patch("zyndai_agent.search.requests.get")
    def test_search_with_all_filters(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": [], "count": 0, "total": 0}
        mock_get.return_value = mock_response

        mgr = SearchAndDiscoveryManager()
        mgr.search_agents(
            keyword="nlp",
            name="Agent",
            capabilities=["nlp", "search"],
            status="ACTIVE",
            did="did:polygonid:test",
            limit=5,
            offset=10,
        )

        params = mock_get.call_args[1]["params"]
        assert params["keyword"] == "nlp"
        assert params["name"] == "Agent"
        assert params["capabilities"] == "nlp,search"
        assert params["status"] == "ACTIVE"
        assert params["did"] == "did:polygonid:test"
        assert params["limit"] == 5
        assert params["offset"] == 10

    @patch("zyndai_agent.search.requests.get")
    def test_search_handles_api_error(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Error"
        mock_get.return_value = mock_response

        mgr = SearchAndDiscoveryManager()
        results = mgr.search_agents(keyword="test")
        assert results == []

    @patch("zyndai_agent.search.requests.get")
    def test_search_handles_network_error(self, mock_get):
        import requests

        mock_get.side_effect = requests.RequestException("Connection refused")

        mgr = SearchAndDiscoveryManager()
        results = mgr.search_agents(keyword="test")
        assert results == []


class TestSearchByCapabilities:
    @patch("zyndai_agent.search.requests.get")
    def test_capabilities_converted_to_keyword(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": [], "count": 0, "total": 0}
        mock_get.return_value = mock_response

        mgr = SearchAndDiscoveryManager()
        mgr.search_agents_by_capabilities(capabilities=["nlp", "search"])

        params = mock_get.call_args[1]["params"]
        assert params["keyword"] == "nlp search"

    @patch("zyndai_agent.search.requests.get")
    def test_empty_capabilities(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": [], "count": 0, "total": 0}
        mock_get.return_value = mock_response

        mgr = SearchAndDiscoveryManager()
        mgr.search_agents_by_capabilities(capabilities=[])
        # keyword should be None, not in params
        params = mock_get.call_args[1]["params"]
        assert "keyword" not in params

    @patch("zyndai_agent.search.requests.get")
    def test_top_k_sets_limit(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": [], "count": 0, "total": 0}
        mock_get.return_value = mock_response

        mgr = SearchAndDiscoveryManager()
        mgr.search_agents_by_capabilities(capabilities=["nlp"], top_k=3)

        params = mock_get.call_args[1]["params"]
        assert params["limit"] == 3


class TestSearchByKeyword:
    @patch("zyndai_agent.search.requests.get")
    def test_search_by_keyword(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [{"id": "1"}],
            "count": 1,
            "total": 1,
        }
        mock_get.return_value = mock_response

        mgr = SearchAndDiscoveryManager()
        results = mgr.search_agents_by_keyword("stock data", limit=5, offset=2)

        assert len(results) == 1
        params = mock_get.call_args[1]["params"]
        assert params["keyword"] == "stock data"
        assert params["limit"] == 5
        assert params["offset"] == 2


class TestGetAgentById:
    @patch("zyndai_agent.search.requests.get")
    def test_get_agent_success(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "agent-1", "name": "Agent"}
        mock_get.return_value = mock_response

        mgr = SearchAndDiscoveryManager()
        result = mgr.get_agent_by_id("agent-1")
        assert result["id"] == "agent-1"
        mock_get.assert_called_once_with("http://localhost:3002/agents/agent-1")

    @patch("zyndai_agent.search.requests.get")
    def test_get_agent_not_found(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        mgr = SearchAndDiscoveryManager()
        result = mgr.get_agent_by_id("nonexistent")
        assert result is None

    @patch("zyndai_agent.search.requests.get")
    def test_get_agent_network_error(self, mock_get):
        import requests

        mock_get.side_effect = requests.RequestException("timeout")

        mgr = SearchAndDiscoveryManager()
        result = mgr.get_agent_by_id("agent-1")
        assert result is None
