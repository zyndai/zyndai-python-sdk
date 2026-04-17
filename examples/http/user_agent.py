"""
User Agent - A conversational assistant that can discover and coordinate with specialist agents.

This agent:
- Chats with the user directly using OpenAI
- Automatically searches the agent network when the user needs a specialist
- Connects, pays (x402) if required, and gets the task done

Usage:
    python examples/http/user_agent.py

Setup:
    zynd init
    zynd card init
    # set ZYND_REGISTRY_URL in .env if not using localhost
"""

from zyndai_agent.agent import AgentConfig, ZyndAIAgent
from zyndai_agent.message import AgentMessage
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool

from dotenv import load_dotenv
import os
import requests
from typing import List

load_dotenv()


# Global reference — set after agent is created
_zynd_agent: ZyndAIAgent = None
_user_agent: "UserAgent" = None


@tool
def search_entities(query: str) -> str:
    """Search the Zynd agent network for specialist agents matching a query.
    Use this when the user needs a specialist (e.g., stock analysis, weather, etc.).
    Returns a list of available agents with their names and descriptions."""
    if not _zynd_agent:
        return "Agent network not initialized."

    agents = _zynd_agent.search_agents_by_keyword(keyword=query, limit=10)
    if not agents:
        return f"No agents found for '{query}'."

    # Store results for connect
    _user_agent.last_search_results = agents

    lines = [f"Found {len(agents)} agent(s):"]
    for i, a in enumerate(agents):
        name = a.get("name", "?")
        summary = a.get("summary", a.get("description", ""))[:100]
        lines.append(f"  [{i + 1}] {name} — {summary}")
    return "\n".join(lines)


@tool
def connect_and_ask(agent_number: int, question: str) -> str:
    """Connect to an agent from search results and ask it a question.
    agent_number is the 1-based index from search results.
    Handles payment (x402) automatically if required."""
    if not _user_agent or not _user_agent.last_search_results:
        return "No search results. Use search_entities first."

    idx = agent_number - 1
    results = _user_agent.last_search_results
    if idx < 0 or idx >= len(results):
        return f"Invalid agent number. Pick 1-{len(results)}."

    agent = results[idx]

    # Connect
    if not agent.get("entity_url") and not agent.get("httpWebhookUrl"):
        return f"Agent {agent.get('name', '?')} has no URL."
    _zynd_agent.connect_agent(agent)

    # Ask
    target_url = _zynd_agent.target_webhook_url
    if not target_url:
        return "Could not determine agent URL."

    try:
        message = AgentMessage(
            content=question,
            sender_id=_zynd_agent.entity_id,
            receiver_id=agent.get("entity_id", agent.get("didIdentifier")),
            message_type="query",
            sender_public_key=(
                _zynd_agent.keypair.public_key_string
                if _zynd_agent.keypair else None
            ),
        )

        response = _user_agent.payment_processor.post(
            target_url,
            json=message.to_dict(),
            headers={"Content-Type": "application/json"},
            timeout=60,
        )

        if response.status_code == 200:
            result = response.json()
            return result.get("response", "No response content")
        return f"Agent returned HTTP {response.status_code}"

    except requests.exceptions.Timeout:
        return "Request timed out"
    except requests.exceptions.ConnectionError:
        return "Connection failed — agent may be offline"
    except Exception as e:
        return f"Error: {e}"


class UserAgent:
    """
    Chat agent with tool-calling. The LLM decides when to search the network
    and delegate to specialist agents vs answering directly.
    """

    def __init__(self, zynd_agent: ZyndAIAgent):
        self.zynd_agent = zynd_agent
        self.payment_processor = zynd_agent.x402_processor
        self.last_search_results: List[dict] = []
        self.chat_history: list = []

        # LLM with tools bound
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.7)
        tools = [search_entities, connect_and_ask]
        self.llm = llm.bind_tools(tools)
        self.tools_by_name = {t.name: t for t in tools}

    def chat(self, message: str) -> str:
        """Process a user message — chat directly or use tools as needed."""
        from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage

        # Add system message if first turn
        if not self.chat_history:
            self.chat_history.append(SystemMessage(content=(
                "You are a helpful assistant connected to the Zynd agent network. "
                "You can chat normally for general questions. "
                "When the user needs a specialist (stock data, market analysis, weather, etc.), "
                "use the search_entities tool to find one, then use connect_and_ask to delegate. "
                "Always pick the most relevant agent from results. "
                "Be concise and helpful."
            )))

        self.chat_history.append(HumanMessage(content=message))

        # LLM loop — may call tools multiple times
        max_iterations = 5
        for _ in range(max_iterations):
            response = self.llm.invoke(self.chat_history)
            self.chat_history.append(response)

            # If no tool calls, we have the final answer
            if not response.tool_calls:
                return response.content

            # Execute tool calls
            for tc in response.tool_calls:
                tool_fn = self.tools_by_name.get(tc["name"])
                if tool_fn:
                    print(f"  [tool] {tc['name']}({tc['args']})")
                    result = tool_fn.invoke(tc["args"])
                    self.chat_history.append(
                        ToolMessage(content=str(result), tool_call_id=tc["id"])
                    )

        # Shouldn't reach here, but just in case
        return "I wasn't able to complete that request. Please try again."


if __name__ == "__main__":
    agent_config = AgentConfig(
        name="User Agent",
        description="A conversational user agent that discovers and coordinates "
        "with specialist agents on the Zynd network.",
        capabilities={
            "ai": ["conversational_ai", "agent_discovery"],
            "protocols": ["http"],
        },
        category="orchestrator",
        tags=["user-agent", "discovery"],
        summary="User-facing agent that finds and coordinates with specialists.",
        webhook_host="0.0.0.0",
        webhook_port=5004,
        registry_url=os.environ.get("ZYND_REGISTRY_URL", "https://dns01.zynd.ai"),
        config_dir=".agent-user",
        use_ngrok=True,
        ngrok_auth_token=os.environ.get("NGROK_AUTH_TOKEN"),
    )

    zynd_agent = ZyndAIAgent(agent_config=agent_config)

    # Set globals for tools
    _zynd_agent = zynd_agent
    client = UserAgent(zynd_agent)
    _user_agent = client

    print("\n" + "=" * 60)
    print("  User Agent")
    print("=" * 60)
    print("\nChat with me! I'll find specialist agents on the network")
    print("when you need them.")
    print(f"\nPayment address: {zynd_agent.pay_to_address}")
    print("\nType 'exit' to quit.")
    print("=" * 60 + "\n")

    while True:
        try:
            user_input = input("You: ").strip()
            if not user_input:
                continue
            if user_input.lower() == "exit":
                print("Goodbye!")
                break

            response = client.chat(user_input)
            print(f"\n{response}\n")

        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()
