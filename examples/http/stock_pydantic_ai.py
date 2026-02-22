"""
Stock Comparison Agent using PydanticAI

This example demonstrates how to use PydanticAI with the ZyndAI Agent SDK.
PydanticAI provides a type-safe approach to building AI agents with
structured outputs and tool definitions.

Features:
- Type-safe agent definitions
- Structured output with Pydantic models
- Tool definitions with type hints
- x402 micropayments (0.0001 USDC per request)
- Ngrok tunnel support for public access

Install PydanticAI:
    pip install pydantic-ai

With ngrok support:
    pip install zyndai-agent[ngrok]

Running multiple agents on the same machine:
    # Terminal 1 - PydanticAI agent on port 5012
    python examples/http/stock_pydantic_ai.py

    # Terminal 2 - LangChain agent on port 5003
    python examples/http/stock_langchain.py

    # Terminal 3 - User agent on port 5004
    python examples/http/user_agent.py

    Each agent gets its own ngrok tunnel and public URL automatically.
"""

from zyndai_agent.agent import AgentConfig, ZyndAIAgent, AgentFramework
from zyndai_agent.message import AgentMessage
from pydantic import BaseModel
from typing import Optional
import httpx

from dotenv import load_dotenv
import os

load_dotenv()


class StockData(BaseModel):
    """Structured stock data model."""

    symbol: str
    name: Optional[str] = None
    price: Optional[float] = None
    change: Optional[str] = None
    analysis: Optional[str] = None


class StockComparison(BaseModel):
    """Structured stock comparison result."""

    stocks: list[StockData]
    comparison: str
    recommendation: str


def create_pydantic_ai_agent():
    """Create a PydanticAI-based stock analysis agent."""
    from pydantic_ai import Agent, RunContext
    from pydantic_ai.models.openai import OpenAIModel

    # Initialize the model
    model = OpenAIModel("gpt-4o-mini")

    # Create the agent with structured output
    agent = Agent(
        model,
        system_prompt="""You are a professional stock comparison and financial analysis agent.

Your capabilities:
- Compare multiple stocks and provide detailed analysis
- Research current stock prices and market data
- Analyze market trends and provide investment insights
- Provide balanced, informative comparisons

When comparing stocks:
1. Identify the stocks mentioned in the query
2. Provide key metrics comparison
3. Summarize market sentiment
4. Give a balanced analysis without providing financial advice

Always be professional and note this is for informational purposes only.""",
        result_type=str,  # Return string for simplicity
    )

    # Define tools
    @agent.tool
    async def search_stock_data(ctx: RunContext[None], query: str) -> str:
        """Search for stock market data and news."""
        # In production, use a real API like Alpha Vantage, Yahoo Finance, etc.
        # For demo, we'll use a mock response
        return (
            f"Search results for '{query}': Stock market data retrieved. "
            f"This is a demo - integrate with real financial APIs for production use."
        )

    @agent.tool
    async def get_stock_price(ctx: RunContext[None], symbol: str) -> str:
        """Get the current price for a stock symbol."""
        # In production, call a real stock API
        # Demo implementation
        demo_prices = {
            "AAPL": {"price": 178.50, "change": "+1.2%"},
            "GOOGL": {"price": 141.25, "change": "+0.8%"},
            "MSFT": {"price": 378.90, "change": "+1.5%"},
            "TSLA": {"price": 248.75, "change": "-0.5%"},
            "AMZN": {"price": 178.25, "change": "+2.1%"},
        }
        symbol = symbol.upper()
        if symbol in demo_prices:
            data = demo_prices[symbol]
            return f"{symbol}: ${data['price']} ({data['change']})"
        return f"{symbol}: Price data not available in demo mode"

    return agent


if __name__ == "__main__":
    # Create agent config with x402 payment and ngrok tunnel
    agent_config = AgentConfig(
        name="Stock Agent (PydanticAI)",
        description="A stock comparison agent built with PydanticAI. "
        "Provides type-safe financial analysis with structured outputs.",
        capabilities={
            "ai": ["nlp", "financial_analysis", "pydantic_ai", "type_safe"],
            "protocols": ["http"],
            "services": ["stock_comparison", "market_research"],
            "domains": ["finance", "stocks"],
        },
        webhook_host="0.0.0.0",
        webhook_port=5012,
        registry_url="https://registry.zynd.ai",
        price="$0.0001",
        api_key=os.environ["ZYND_API_KEY"],
        config_dir=".agent-pydantic-ai",
        # Enable ngrok to expose this agent publicly (requires: pip install zyndai-agent[ngrok])
        # Each agent on a different port gets its own ngrok tunnel URL
        use_ngrok=True,
        ngrok_auth_token=os.environ.get(
            "NGROK_AUTH_TOKEN"
        ),  # Or set globally via: ngrok config add-authtoken <token>
    )

    # Initialize ZyndAI agent
    zynd_agent = ZyndAIAgent(agent_config=agent_config)

    # Create and set the PydanticAI agent
    pydantic_agent = create_pydantic_ai_agent()
    zynd_agent.set_pydantic_ai_agent(pydantic_agent)

    # Message handler
    def message_handler(message: AgentMessage, topic: str):
        import traceback

        print(f"\n{'=' * 60}")
        print(f"[PydanticAI] Received: {message.content}")
        print(f"{'=' * 60}\n")

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
    print("\n" + "=" * 60)
    print("Stock Agent (PydanticAI) is running")
    print(f"Framework: PydanticAI (Type-Safe)")
    print(f"Price: 0.0001 USDC per request")
    print(f"Webhook: {zynd_agent.webhook_url}")
    print("=" * 60)
    print("\nType 'exit' to quit\n")

    while True:
        cmd = input("Command: ")
        if cmd.lower() == "exit":
            break
