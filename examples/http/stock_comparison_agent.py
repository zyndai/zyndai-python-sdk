"""
Stock Comparison Agent - A paid agent that compares stocks and provides financial analysis.

This agent:
- Charges 0.0001 USDC per request on Base Sepolia via x402 protocol
- Provides stock comparison and financial analysis capabilities
- Can be discovered by other agents through the registry via semantic search

Run this agent first before running user_agent.py
"""

from zyndai_agent.agent import AgentConfig, ZyndAIAgent
from zyndai_agent.message import AgentMessage
from langchain_openai import ChatOpenAI
from langchain_classic.memory import ChatMessageHistory
from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool
from langchain_community.tools.tavily_search import TavilySearchResults

from dotenv import load_dotenv
import os
from typing import List

load_dotenv()


# Custom tool for stock comparison
@tool
def compare_stocks(stock_symbols: str) -> str:
    """
    Compare multiple stocks by their key metrics.
    Input should be comma-separated stock symbols like 'AAPL,GOOGL,MSFT'.
    Returns a comparison analysis of the stocks.
    """
    symbols = [s.strip().upper() for s in stock_symbols.split(',')]

    # This is a mock comparison - in production, you would fetch real data from a financial API
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


if __name__ == "__main__":

    # Create agent config with webhook settings and x402 payment
    # Price: 0.0001 USDC per request on Base Sepolia
    # Using separate config_dir so this agent has its own identity
    agent_config = AgentConfig(
        name="Stock Comparison Agent",
        description="A professional stock comparison and financial analysis agent. "
                    "Compares stocks, analyzes market trends, and provides investment insights. "
                    "Specializes in stock comparison, equity analysis, and market research.",
        capabilities={
            "ai": ["nlp", "financial_analysis", "data_analysis"],
            "protocols": ["http"],
            "services": [
                "stock_comparison",
                "financial_analysis",
                "market_research",
                "equity_analysis",
                "investment_analysis"
            ],
            "domains": [
                "finance",
                "stocks",
                "investing",
                "market_data",
                "trading"
            ]
        },
        webhook_host="0.0.0.0",
        webhook_port=5003,
        auto_reconnect=True,
        message_history_limit=100,
        registry_url="https://registry.zynd.ai",
        price="$0.0001",  # 0.0001 USDC per request
        api_key=os.environ["ZYND_API_KEY"],
        config_dir=".agent-stock"  # Separate identity from other agents
    )

    # Init zynd agent sdk wrapper
    zynd_agent = ZyndAIAgent(agent_config=agent_config)

    # Create a langchain agent with stock analysis capabilities
    llm = ChatOpenAI(model="gpt-3.5-turbo", temperature=0)

    # Tools for stock analysis
    search_tool = TavilySearchResults(max_results=5)

    tools = [compare_stocks, get_stock_info, search_tool]

    # Create message history store
    message_history = ChatMessageHistory()

    # Create prompt template for stock analysis
    prompt = ChatPromptTemplate.from_messages([
        ("system", """You are a professional stock comparison and financial analysis agent.

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

Always be professional, accurate, and note that this is for informational purposes only."""),
        MessagesPlaceholder(variable_name="chat_history"),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad")
    ])

    # Create agent with tool calling
    agent = create_tool_calling_agent(llm, tools, prompt)
    agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True)

    zynd_agent.set_agent_executor(agent_executor)


    def message_handler(message: AgentMessage, topic: str):
        """Handle incoming stock comparison requests."""
        import traceback

        print(f"\n{'='*60}")
        print(f"Received request from: {message.sender_id}")
        print(f"Query: {message.content}")
        print(f"{'='*60}\n")

        try:
            # Add user message to history
            message_history.add_user_message(message.content)

            # Process the stock comparison request
            agent_response = zynd_agent.agent_executor.invoke({
                "input": message.content,
                "chat_history": message_history.messages
            })
            agent_output = agent_response["output"]

            # Add AI response to history
            message_history.add_ai_message(agent_output)

            print(f"\nResponse: {agent_output}\n")

            # Set the response for synchronous mode
            zynd_agent.set_response(message.message_id, agent_output)

            # Also send via webhook if target is connected (for agent-to-agent communication)
            if zynd_agent.target_webhook_url:
                zynd_agent.send_message(agent_output)

        except Exception as e:
            error_msg = f"Error processing request: {str(e)}"
            print(f"\n{'!'*60}")
            print(f"ERROR: {error_msg}")
            print(f"Traceback:\n{traceback.format_exc()}")
            print(f"{'!'*60}\n")

            # Still set a response so the client doesn't hang
            zynd_agent.set_response(message.message_id, f"Error: {str(e)}")

    zynd_agent.add_message_handler(message_handler)


    # Main loop - keep the agent running
    print("\n" + "="*60)
    print("Stock Comparison Agent is running and ready to receive requests")
    print(f"Charging: 0.0001 USDC per request (Base Sepolia)")
    print(f"Payment Address: {zynd_agent.pay_to_address}")
    print("="*60)
    print("\nType 'Exit' to quit\n")

    while True:
        user_input = input("Command (Exit to quit): ")

        if user_input.lower() == "exit":
            print("Shutting down Stock Comparison Agent...")
            break
