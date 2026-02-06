"""
Stock Comparison Agent using CrewAI

This example demonstrates how to use CrewAI with the ZyndAI Agent SDK.
CrewAI provides a multi-agent framework where specialized agents
collaborate to complete complex tasks.

Features:
- Multi-agent collaboration (researcher + analyst)
- Role-based task assignment
- Automatic task delegation
- x402 micropayments (0.0001 USDC per request)

Install CrewAI:
    pip install crewai crewai-tools
"""

from zyndai_agent.agent import AgentConfig, ZyndAIAgent, AgentFramework
from zyndai_agent.message import AgentMessage

from dotenv import load_dotenv
import os

load_dotenv()


def create_crewai_agent():
    """Create a CrewAI-based stock analysis crew."""
    from crewai import Agent, Task, Crew, Process
    from crewai_tools import SerperDevTool

    # Initialize search tool
    search_tool = SerperDevTool()

    # Create specialized agents
    researcher = Agent(
        role="Stock Market Researcher",
        goal="Research and gather comprehensive stock market data and news",
        backstory="""You are an expert stock market researcher with years of
        experience in gathering financial data. You excel at finding current
        stock prices, market trends, and relevant news.""",
        tools=[search_tool],
        verbose=True
    )

    analyst = Agent(
        role="Financial Analyst",
        goal="Analyze stock data and provide comprehensive comparisons",
        backstory="""You are a senior financial analyst with expertise in
        stock valuation and comparison. You provide balanced, professional
        analysis without giving direct financial advice.""",
        verbose=True
    )

    # Define tasks
    research_task = Task(
        description="""Research the stocks mentioned in the query: {query}

        Gather:
        - Current stock prices
        - Recent price changes
        - Market capitalization
        - Key news and developments

        Provide raw data for analysis.""",
        expected_output="Comprehensive stock data including prices, changes, and news",
        agent=researcher
    )

    analysis_task = Task(
        description="""Based on the research data, provide a detailed stock comparison.

        Include:
        - Price comparison
        - Performance analysis
        - Market position
        - Key differences and similarities
        - Balanced summary (not financial advice)

        Query: {query}""",
        expected_output="Professional stock comparison analysis",
        agent=analyst
    )

    # Create the crew
    crew = Crew(
        agents=[researcher, analyst],
        tasks=[research_task, analysis_task],
        process=Process.sequential,
        verbose=True
    )

    return crew


if __name__ == "__main__":

    # Create agent config with x402 payment
    agent_config = AgentConfig(
        name="Stock Agent (CrewAI)",
        description="A stock comparison agent built with CrewAI. "
                    "Uses multiple AI agents (researcher + analyst) for comprehensive analysis.",
        capabilities={
            "ai": ["nlp", "financial_analysis", "crewai", "multi_agent"],
            "protocols": ["http"],
            "services": ["stock_comparison", "market_research"],
            "domains": ["finance", "stocks"]
        },
        webhook_host="0.0.0.0",
        webhook_port=5011,
        registry_url="https://registry.zynd.ai",
        price="$0.0001",
        api_key=os.environ["ZYND_API_KEY"],
        config_dir=".agent-crewai"
    )

    # Initialize ZyndAI agent
    zynd_agent = ZyndAIAgent(agent_config=agent_config)

    # Create and set the CrewAI agent
    crew = create_crewai_agent()
    zynd_agent.set_crewai_agent(crew)

    # Message handler
    def message_handler(message: AgentMessage, topic: str):
        import traceback

        print(f"\n{'='*60}")
        print(f"[CrewAI] Received: {message.content}")
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
    print("Stock Agent (CrewAI) is running")
    print(f"Framework: CrewAI (Multi-Agent)")
    print(f"Agents: Researcher + Analyst")
    print(f"Price: 0.0001 USDC per request")
    print(f"Webhook: {zynd_agent.webhook_url}")
    print("="*60)
    print("\nType 'exit' to quit\n")

    while True:
        cmd = input("Command: ")
        if cmd.lower() == "exit":
            break
