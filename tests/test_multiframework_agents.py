"""
Tests for multiframework agent support in ZyndAI Agent SDK.

Tests the invoke() method dispatching across all supported frameworks:
- LangChain
- LangGraph
- CrewAI
- PydanticAI
- Custom callable
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from zyndai_agent.agent import AgentFramework, AgentConfig, ZyndAIAgent


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

FAKE_CONFIG = {
    "id": "test-agent-id",
    "didIdentifier": "did:polygonid:test",
    "did": {
        "issuer": "did:polygonid:test:issuer",
        "id": "test-cred-id",
        "credentialSubject": {"x": "123", "y": "456", "type": "AuthBJJCredential"},
        "type": ["VerifiableCredential", "AuthBJJCredential"],
    },
    "name": "Test Agent",
    "description": "A test agent",
    "seed": "dGVzdHNlZWQxMjM0NTY3ODkwMTIzNDU2Nzg5MDEyMzQ=",  # 32 bytes base64
}


@pytest.fixture
def mock_agent():
    """Create a ZyndAIAgent with all external dependencies mocked out."""
    with (
        patch(
            "zyndai_agent.agent.ConfigManager.load_or_create", return_value=FAKE_CONFIG
        ),
        patch("zyndai_agent.agent.X402PaymentProcessor") as mock_x402,
        patch("zyndai_agent.agent.IdentityManager.__init__", return_value=None),
        patch(
            "zyndai_agent.agent.SearchAndDiscoveryManager.__init__", return_value=None
        ),
        patch(
            "zyndai_agent.agent.WebhookCommunicationManager.__init__", return_value=None
        ),
        patch("zyndai_agent.agent.requests.patch") as mock_req_patch,
    ):
        mock_x402_inst = MagicMock()
        mock_x402_inst.account.address = "0xFakeAddress"
        mock_x402.return_value = mock_x402_inst

        mock_req_patch.return_value = MagicMock(status_code=200)

        config = AgentConfig(
            name="TestAgent",
            description="Test",
            webhook_port=15000,
            api_key="test-key",
            registry_url="http://localhost:3002",
        )

        # Set webhook_url as instance attribute before __init__ calls _display_agent_info
        # Since WebhookCommunicationManager.__init__ is mocked, we need to set it manually
        agent = ZyndAIAgent.__new__(ZyndAIAgent)
        agent.webhook_url = "http://localhost:15000/webhook"
        ZyndAIAgent.__init__(agent, config)

        return agent


# ---------------------------------------------------------------------------
# AgentFramework enum tests
# ---------------------------------------------------------------------------


class TestAgentFramework:
    def test_enum_values(self):
        assert AgentFramework.LANGCHAIN.value == "langchain"
        assert AgentFramework.LANGGRAPH.value == "langgraph"
        assert AgentFramework.CREWAI.value == "crewai"
        assert AgentFramework.PYDANTIC_AI.value == "pydantic_ai"
        assert AgentFramework.CUSTOM.value == "custom"

    def test_enum_count(self):
        assert len(AgentFramework) == 5

    def test_string_comparison(self):
        """AgentFramework extends str, so it should be comparable to strings."""
        assert AgentFramework.LANGCHAIN == "langchain"
        assert AgentFramework.CREWAI == "crewai"


# ---------------------------------------------------------------------------
# setter method tests
# ---------------------------------------------------------------------------


class TestSetterMethods:
    def test_set_langchain_agent(self, mock_agent):
        executor = MagicMock()
        mock_agent.set_langchain_agent(executor)
        assert mock_agent.agent_executor is executor
        assert mock_agent.agent_framework == AgentFramework.LANGCHAIN

    def test_set_langgraph_agent(self, mock_agent):
        graph = MagicMock()
        mock_agent.set_langgraph_agent(graph)
        assert mock_agent.agent_executor is graph
        assert mock_agent.agent_framework == AgentFramework.LANGGRAPH

    def test_set_crewai_agent(self, mock_agent):
        crew = MagicMock()
        mock_agent.set_crewai_agent(crew)
        assert mock_agent.agent_executor is crew
        assert mock_agent.agent_framework == AgentFramework.CREWAI

    def test_set_pydantic_ai_agent(self, mock_agent):
        pydantic_agent = MagicMock()
        mock_agent.set_pydantic_ai_agent(pydantic_agent)
        assert mock_agent.agent_executor is pydantic_agent
        assert mock_agent.agent_framework == AgentFramework.PYDANTIC_AI

    def test_set_custom_agent(self, mock_agent):
        fn = lambda x: f"echo: {x}"
        mock_agent.set_custom_agent(fn)
        assert mock_agent.custom_invoke_fn is fn
        assert mock_agent.agent_framework == AgentFramework.CUSTOM

    def test_set_agent_executor_generic(self, mock_agent):
        executor = MagicMock()
        mock_agent.set_agent_executor(executor, AgentFramework.CREWAI)
        assert mock_agent.agent_executor is executor
        assert mock_agent.agent_framework == AgentFramework.CREWAI

    def test_set_agent_executor_default_framework(self, mock_agent):
        executor = MagicMock()
        mock_agent.set_agent_executor(executor)
        assert mock_agent.agent_framework == AgentFramework.LANGCHAIN


# ---------------------------------------------------------------------------
# invoke() dispatch tests — the core multiframework logic
# ---------------------------------------------------------------------------


class TestInvokeLangChain:
    def test_invoke_returns_output_key(self, mock_agent):
        executor = MagicMock()
        executor.invoke.return_value = {"output": "LangChain answer"}
        mock_agent.set_langchain_agent(executor)

        result = mock_agent.invoke("What is AI?")
        assert result == "LangChain answer"
        executor.invoke.assert_called_once_with({"input": "What is AI?"})

    def test_invoke_falls_back_when_no_output_key(self, mock_agent):
        executor = MagicMock()
        executor.invoke.return_value = {"result": "fallback"}
        mock_agent.set_langchain_agent(executor)

        result = mock_agent.invoke("test")
        assert "fallback" in result  # str(dict) representation

    def test_invoke_passes_kwargs(self, mock_agent):
        executor = MagicMock()
        executor.invoke.return_value = {"output": "ok"}
        mock_agent.set_langchain_agent(executor)

        mock_agent.invoke("test", temperature=0.5)
        executor.invoke.assert_called_once_with({"input": "test", "temperature": 0.5})


class TestInvokeLangGraph:
    def test_invoke_extracts_last_message_content(self, mock_agent):
        graph = MagicMock()
        last_msg = MagicMock()
        last_msg.content = "LangGraph answer"
        graph.invoke.return_value = {"messages": [MagicMock(), last_msg]}
        mock_agent.set_langgraph_agent(graph)

        result = mock_agent.invoke("query")
        assert result == "LangGraph answer"
        graph.invoke.assert_called_once_with({"messages": [("user", "query")]})

    def test_invoke_handles_message_without_content_attr(self, mock_agent):
        graph = MagicMock()
        graph.invoke.return_value = {"messages": ["plain string message"]}
        mock_agent.set_langgraph_agent(graph)

        result = mock_agent.invoke("q")
        assert result == "plain string message"

    def test_invoke_handles_empty_messages(self, mock_agent):
        graph = MagicMock()
        graph.invoke.return_value = {"messages": []}
        mock_agent.set_langgraph_agent(graph)

        result = mock_agent.invoke("q")
        # Falls through to str(result)
        assert "messages" in result

    def test_invoke_handles_no_messages_key(self, mock_agent):
        graph = MagicMock()
        graph.invoke.return_value = {"other": "data"}
        mock_agent.set_langgraph_agent(graph)

        result = mock_agent.invoke("q")
        assert "other" in result


class TestInvokeCrewAI:
    def test_invoke_returns_raw_attribute(self, mock_agent):
        crew = MagicMock()
        crew_output = MagicMock()
        crew_output.raw = "CrewAI analysis result"
        crew.kickoff.return_value = crew_output
        mock_agent.set_crewai_agent(crew)

        result = mock_agent.invoke("analyze stocks")
        assert result == "CrewAI analysis result"
        crew.kickoff.assert_called_once_with(inputs={"query": "analyze stocks"})

    def test_invoke_falls_back_to_str_when_no_raw(self, mock_agent):
        crew = MagicMock()

        class CrewOutputNoRaw:
            def __str__(self):
                return "stringified crew output"

        crew.kickoff.return_value = CrewOutputNoRaw()
        mock_agent.set_crewai_agent(crew)

        result = mock_agent.invoke("test")
        assert result == "stringified crew output"

    def test_invoke_passes_kwargs_to_kickoff(self, mock_agent):
        crew = MagicMock()
        crew_output = MagicMock()
        crew_output.raw = "ok"
        crew.kickoff.return_value = crew_output
        mock_agent.set_crewai_agent(crew)

        mock_agent.invoke("q", verbose=True)
        crew.kickoff.assert_called_once_with(inputs={"query": "q", "verbose": True})


class TestInvokePydanticAI:
    def test_invoke_returns_data_attribute(self, mock_agent):
        agent = MagicMock()
        run_result = MagicMock()
        run_result.data = "PydanticAI structured output"
        agent.run_sync.return_value = run_result
        mock_agent.set_pydantic_ai_agent(agent)

        result = mock_agent.invoke("query")
        assert result == "PydanticAI structured output"
        agent.run_sync.assert_called_once_with("query")

    def test_invoke_falls_back_to_str_when_no_data(self, mock_agent):
        agent = MagicMock()

        class RunResultNoData:
            def __str__(self):
                return "stringified result"

        agent.run_sync.return_value = RunResultNoData()
        mock_agent.set_pydantic_ai_agent(agent)

        result = mock_agent.invoke("test")
        assert result == "stringified result"

    def test_invoke_passes_kwargs(self, mock_agent):
        agent = MagicMock()
        run_result = MagicMock()
        run_result.data = "ok"
        agent.run_sync.return_value = run_result
        mock_agent.set_pydantic_ai_agent(agent)

        mock_agent.invoke("q", model="gpt-4")
        agent.run_sync.assert_called_once_with("q", model="gpt-4")


class TestInvokeCustom:
    def test_invoke_calls_custom_function(self, mock_agent):
        fn = MagicMock(return_value="custom result")
        mock_agent.set_custom_agent(fn)

        result = mock_agent.invoke("hello")
        assert result == "custom result"
        fn.assert_called_once_with("hello")

    def test_invoke_raises_when_no_custom_fn_set(self, mock_agent):
        mock_agent.agent_framework = AgentFramework.CUSTOM
        mock_agent.custom_invoke_fn = None

        with pytest.raises(ValueError, match="Custom agent invoke function not set"):
            mock_agent.invoke("test")

    def test_invoke_with_lambda(self, mock_agent):
        mock_agent.set_custom_agent(lambda x: x.upper())
        assert mock_agent.invoke("hello") == "HELLO"

    def test_invoke_with_complex_custom_function(self, mock_agent):
        def complex_fn(text):
            return f"Processed: {text} | Length: {len(text)}"

        mock_agent.set_custom_agent(complex_fn)
        result = mock_agent.invoke("test input")
        assert result == "Processed: test input | Length: 10"


class TestInvokeErrors:
    def test_invoke_raises_on_unknown_framework(self, mock_agent):
        mock_agent.agent_framework = "nonexistent_framework"

        with pytest.raises(ValueError, match="Unknown agent framework"):
            mock_agent.invoke("test")

    def test_invoke_raises_on_none_framework(self, mock_agent):
        mock_agent.agent_framework = None

        with pytest.raises(ValueError, match="Unknown agent framework"):
            mock_agent.invoke("test")


# ---------------------------------------------------------------------------
# Framework switching tests
# ---------------------------------------------------------------------------


class TestFrameworkSwitching:
    """Test that agents can switch between frameworks dynamically."""

    def test_switch_from_langchain_to_crewai(self, mock_agent):
        # Start with LangChain
        lc_executor = MagicMock()
        lc_executor.invoke.return_value = {"output": "LC result"}
        mock_agent.set_langchain_agent(lc_executor)
        assert mock_agent.invoke("q") == "LC result"

        # Switch to CrewAI
        crew = MagicMock()
        crew_output = MagicMock()
        crew_output.raw = "Crew result"
        crew.kickoff.return_value = crew_output
        mock_agent.set_crewai_agent(crew)
        assert mock_agent.invoke("q") == "Crew result"

    def test_switch_from_custom_to_pydantic_ai(self, mock_agent):
        # Start with custom
        mock_agent.set_custom_agent(lambda x: "custom")
        assert mock_agent.invoke("q") == "custom"

        # Switch to PydanticAI
        pai = MagicMock()
        result = MagicMock()
        result.data = "pydantic result"
        pai.run_sync.return_value = result
        mock_agent.set_pydantic_ai_agent(pai)
        assert mock_agent.invoke("q") == "pydantic result"

    def test_all_frameworks_in_sequence(self, mock_agent):
        """Verify that all 5 frameworks can be set and invoked sequentially."""
        results = []

        # LangChain
        lc = MagicMock()
        lc.invoke.return_value = {"output": "1"}
        mock_agent.set_langchain_agent(lc)
        results.append(mock_agent.invoke("q"))

        # LangGraph
        lg = MagicMock()
        msg = MagicMock()
        msg.content = "2"
        lg.invoke.return_value = {"messages": [msg]}
        mock_agent.set_langgraph_agent(lg)
        results.append(mock_agent.invoke("q"))

        # CrewAI
        cr = MagicMock()
        co = MagicMock()
        co.raw = "3"
        cr.kickoff.return_value = co
        mock_agent.set_crewai_agent(cr)
        results.append(mock_agent.invoke("q"))

        # PydanticAI
        pai = MagicMock()
        pr = MagicMock()
        pr.data = "4"
        pai.run_sync.return_value = pr
        mock_agent.set_pydantic_ai_agent(pai)
        results.append(mock_agent.invoke("q"))

        # Custom
        mock_agent.set_custom_agent(lambda x: "5")
        results.append(mock_agent.invoke("q"))

        assert results == ["1", "2", "3", "4", "5"]
