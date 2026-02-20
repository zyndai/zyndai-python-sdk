"""
Stock Comparison Agent using LangGraph

This example demonstrates how to use LangGraph with the ZyndAI Agent SDK.
LangGraph provides a graph-based approach to building AI agents with
explicit state management and control flow.

Features:
- Graph-based agent architecture
- Explicit state management
- Tool calling with search capabilities
- x402 micropayments (0.0001 USDC per request)
"""

from zyndai_agent.agent import AgentConfig, ZyndAIAgent, AgentFramework
from zyndai_agent.message import AgentMessage
from langchain_openai import ChatOpenAI
from langchain_community.tools.tavily_search import TavilySearchResults
from langgraph.graph import StateGraph, MessagesState, START, END
from langgraph.prebuilt import ToolNode, tools_condition

from dotenv import load_dotenv
import os

load_dotenv()


def create_langgraph_agent():
    """Create a LangGraph-based stock analysis agent."""

    # Initialize the LLM
    llm = ChatOpenAI(model="gpt-3.5-turbo", temperature=0)

    # Define tools
    search_tool = TavilySearchResults(max_results=5)
    tools = [search_tool]

    # Bind tools to the LLM
    llm_with_tools = llm.bind_tools(tools)

    # Define the agent node
    def agent_node(state: MessagesState):
        """Process messages and decide on tool use or response."""
        system_message = {
            "role": "system",
            "content": """You are a professional stock comparison and financial analysis agent.

Your capabilities:
- Compare multiple stocks and provide detailed analysis
- Research current stock prices and market data using search
- Analyze market trends and provide investment insights
- Provide balanced, informative comparisons

When comparing stocks:
1. First use search to get the latest stock prices and market data
2. Provide key metrics comparison (price, market cap, trends)
3. Summarize recent news and market sentiment
4. Give a balanced analysis without providing financial advice

Always be professional and note this is for informational purposes only."""
        }

        messages = [system_message] + state["messages"]
        response = llm_with_tools.invoke(messages)
        return {"messages": [response]}

    # Create the graph
    graph = StateGraph(MessagesState)

    # Add nodes
    graph.add_node("agent", agent_node)
    graph.add_node("tools", ToolNode(tools))

    # Add edges
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition)
    graph.add_edge("tools", "agent")

    # Compile the graph
    return graph.compile()


if __name__ == "__main__":

    # Create agent config with x402 payment
    agent_config = AgentConfig(
        name="Stock Agent (LangGraph)",
        description="A stock comparison agent built with LangGraph. "
                    "Provides financial analysis using graph-based AI architecture.",
        capabilities={
            "ai": ["nlp", "financial_analysis", "langgraph"],
            "protocols": ["http"],
            "services": ["stock_comparison", "market_research"],
            "domains": ["finance", "stocks"]
        },
        webhook_host="0.0.0.0",
        webhook_port=5010,
        registry_url="https://registry.zynd.ai",
        price="$0.0001",
        api_key=os.environ["ZYND_API_KEY"],
        config_dir=".agent-langgraph"
    )

    # Initialize ZyndAI agent
    zynd_agent = ZyndAIAgent(agent_config=agent_config)

    # Create and set the LangGraph agent
    langgraph_agent = create_langgraph_agent()
    zynd_agent.set_langgraph_agent(langgraph_agent)

    # Message handler
    def message_handler(message: AgentMessage, topic: str):
        import traceback

        print(f"\n{'='*60}")
        print(f"[LangGraph] Received: {message.content}")
        print(f"{'='*60}\n")

        try:
            # Use the unified invoke method
            response = zynd_agent.invoke(message.content)
            print(f"\nResponse: {response}\n")
            zynd_agent.set_response(message.message_id, response)

        except Exception as e:
            print(f"ERROR: {e}")
            print(traceback.format_exc())
            zynd_agent.set_response(message.message_id, f"Error: {str(e)}")

    zynd_agent.add_message_handler(message_handler)

    # Keep running
    print("\n" + "="*60)
    print("Stock Agent (LangGraph) is running")
    print(f"Framework: LangGraph")
    print(f"Price: 0.0001 USDC per request")
    print(f"Webhook: {zynd_agent.webhook_url}")
    print("="*60)
    print("\nType 'exit' to quit\n")

    while True:
        cmd = input("Command: ")
        if cmd.lower() == "exit":
            break
