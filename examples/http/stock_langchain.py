"""
Stock Comparison Agent using LangChain

This example demonstrates how to use LangChain with the ZyndAI Agent SDK.
LangChain provides a flexible framework for building AI agents with tools,
memory, and various LLM integrations.

Features:
- Tool-calling agent with custom and pre-built tools
- Chat memory for conversation history
- Search capabilities via Tavily
- x402 micropayments (0.0001 USDC per request)
- Ngrok tunnel support for public access

Install LangChain:
    pip install langchain langchain-openai langchain-community langchain-classic

With ngrok support:
    pip install zyndai-agent[ngrok]

Running multiple agents on the same machine:
    # Terminal 1 - LangChain agent on port 5003
    python examples/http/stock_langchain.py

    # Terminal 2 - CrewAI agent on port 5011
    python examples/http/stock_crewai.py

    # Terminal 3 - User agent on port 5004
    python examples/http/user_agent.py

    Each agent gets its own ngrok tunnel and public URL automatically.
"""

from zyndai_agent.agent import AgentConfig, ZyndAIAgent, AgentFramework
from zyndai_agent.message import AgentMessage
from langchain_openai import ChatOpenAI
from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool
from langchain_community.tools.tavily_search import TavilySearchResults

from dotenv import load_dotenv
import os

load_dotenv()


# Custom tool for stock comparison
@tool
def compare_stocks(stock_symbols: str) -> str:
    """
    Compare multiple stocks by their key metrics.
    Input should be comma-separated stock symbols like 'AAPL,GOOGL,MSFT'.
    Returns a comparison analysis of the stocks.
    """
    symbols = [s.strip().upper() for s in stock_symbols.split(",")]
    comparison = f"Stock Comparison Analysis for: {', '.join(symbols)}\n\n"
    comparison += "Note: Using search to get latest market data...\n"
    return comparison


@tool
def get_stock_info(symbol: str) -> str:
    """
    Get detailed information about a specific stock.
    Input should be a single stock symbol like 'AAPL'.
    """
    symbol = symbol.strip().upper()
    return f"Fetching detailed information for {symbol}..."


def create_langchain_agent():
    """Create a LangChain-based stock analysis agent."""

    # Initialize LLM
    llm = ChatOpenAI(model="gpt-3.5-turbo", temperature=0)

    # Tools for stock analysis
    search_tool = TavilySearchResults(max_results=5)
    tools = [compare_stocks, get_stock_info, search_tool]

    # Create prompt template
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                """You are a professional stock comparison and financial analysis agent.

Your capabilities:
- Compare multiple stocks and provide detailed analysis
- Research current stock prices and market data using search
- Analyze market trends and provide investment insights
- Provide balanced, informative comparisons

When comparing stocks:
1. First use search to get the latest stock prices and market data
2. Provide key metrics comparison (price, market cap, P/E ratio if available)
3. Summarize recent news and market sentiment
4. Give a balanced analysis without providing financial advice

Always be professional and note this is for informational purposes only.""",
            ),
            MessagesPlaceholder(variable_name="chat_history"),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ]
    )

    # Create agent executor
    agent = create_tool_calling_agent(llm, tools, prompt)
    return AgentExecutor(agent=agent, tools=tools, verbose=True)


if __name__ == "__main__":
    # Create agent config with x402 payment and ngrok tunnel
    agent_config = AgentConfig(
        name="Stock Agent (LangChain)",
        description="A stock comparison agent built with LangChain. "
        "Provides financial analysis with tool calling and search capabilities.",
        capabilities={
            "ai": ["nlp", "financial_analysis", "langchain"],
            "protocols": ["http"],
            "services": ["stock_comparison", "market_research"],
            "domains": ["finance", "stocks"],
        },
        webhook_host="0.0.0.0",
        webhook_port=5003,
        registry_url="https://registry.zynd.ai",
        price="$0.0001",
        api_key=os.environ["ZYND_API_KEY"],
        config_dir=".agent-langchain",
        # Enable ngrok to expose this agent publicly (requires: pip install zyndai-agent[ngrok])
        # Each agent on a different port gets its own ngrok tunnel URL
        use_ngrok=True,
        ngrok_auth_token=os.environ.get(
            "NGROK_AUTH_TOKEN"
        ),  # Or set globally via: ngrok config add-authtoken <token>
    )

    # Initialize ZyndAI agent
    zynd_agent = ZyndAIAgent(agent_config=agent_config)

    # Create and set the LangChain agent
    agent_executor = create_langchain_agent()
    zynd_agent.set_langchain_agent(agent_executor)

    # Message handler
    def message_handler(message: AgentMessage, topic: str):
        import traceback

        print(f"\n{'=' * 60}")
        print(f"[LangChain] Received: {message.content}")
        print(f"{'=' * 60}\n")

        try:
            # Use the unified invoke method
            response = zynd_agent.invoke(message.content, chat_history=[])
            print(f"\nResponse: {response}\n")
            zynd_agent.set_response(message.message_id, response)

        except Exception as e:
            print(f"ERROR: {e}")
            print(traceback.format_exc())
            zynd_agent.set_response(message.message_id, f"Error: {str(e)}")

    zynd_agent.add_message_handler(message_handler)

    # Keep running
    print("\n" + "=" * 60)
    print("Stock Agent (LangChain) is running")
    print(f"Framework: LangChain")
    print(f"Price: 0.0001 USDC per request")
    print(f"Webhook: {zynd_agent.webhook_url}")
    print("=" * 60)
    print("\nType 'exit' to quit\n")

    while True:
        cmd = input("Command: ")
        if cmd.lower() == "exit":
            break
