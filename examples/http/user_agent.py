"""
User Agent - An interactive agent that searches for and communicates with specialized agents.

This agent:
- Takes questions from the terminal (stdio)
- Searches the registry for relevant agents (e.g., stock comparison agents)
- Automatically pays the required fee (0.0001 USDC on Base Sepolia) via x402
- Forwards user questions and returns the responses

Run stock_comparison_agent.py first, then run this agent.
"""

from zyndai_agent.agent import AgentConfig, ZyndAIAgent
from zyndai_agent.message import AgentMessage
from zyndai_agent.payment import X402PaymentProcessor
from langchain_openai import ChatOpenAI
from langchain_classic.memory import ChatMessageHistory
from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool

from dotenv import load_dotenv
import os
import json
import requests
from typing import Optional

load_dotenv()


class StockComparisonClient:
    """Client for interacting with stock comparison agents via registry search."""

    def __init__(self, zynd_agent: ZyndAIAgent):
        self.zynd_agent = zynd_agent
        self.connected_agent = None
        self.payment_processor = zynd_agent.x402_processor

    def search_for_stock_agent(self) -> Optional[dict]:
        """Search the registry for a stock comparison agent."""
        print("\nSearching registry for stock comparison agents...")

        # Search using relevant keywords/capabilities
        search_terms = [
            "stock comparison",
            "financial analysis",
            "stock analysis",
            "equity analysis",
            "market research"
        ]

        agents = self.zynd_agent.search_agents_by_capabilities(
            capabilities=search_terms,
            top_k=5
        )

        if not agents:
            print("No stock comparison agents found in the registry.")
            return None

        print(f"\nFound {len(agents)} matching agent(s):")
        for i, agent in enumerate(agents):
            status = agent.get('status', 'UNKNOWN')
            print(f"  {i+1}. {agent.get('name', 'Unknown')} [{status}]")
            print(f"      Description: {agent.get('description', 'N/A')[:80]}...")
            print(f"      Webhook: {agent.get('httpWebhookUrl', 'N/A')}")

        # Return the best matching agent
        return agents[0]

    def connect_to_agent(self, agent: dict) -> bool:
        """Connect to a specific agent."""
        if not agent.get('httpWebhookUrl'):
            print(f"Agent {agent.get('name', 'Unknown')} has no webhook URL.")
            return False

        self.connected_agent = agent
        self.zynd_agent.connect_agent(agent)
        print(f"\nConnected to: {agent.get('name', 'Unknown')}")
        return True

    def ask_stock_question(self, question: str) -> str:
        """
        Ask a stock-related question to the connected agent.
        Automatically handles x402 payment.
        """
        if not self.connected_agent:
            return "Error: Not connected to any stock agent. Run search first."

        webhook_url = self.connected_agent.get('httpWebhookUrl')
        if not webhook_url:
            return "Error: Connected agent has no webhook URL."

        # Use sync endpoint for immediate response
        sync_url = webhook_url.replace('/webhook', '/webhook/sync')

        print(f"\nSending question to {self.connected_agent.get('name', 'Unknown')}...")
        print(f"Payment will be processed automatically via x402 (0.0001 USDC)")

        try:
            # Create the message payload
            message = AgentMessage(
                content=question,
                sender_id=self.zynd_agent.agent_id,
                receiver_id=self.connected_agent.get('didIdentifier'),
                message_type="query",
                sender_did=self.zynd_agent.identity_credential
            )

            # Use the x402 payment processor to make the request
            # This automatically handles 402 Payment Required responses
            # Note: use to_dict() not to_json() - json= parameter expects a dict
            response = self.payment_processor.post(
                sync_url,
                json=message.to_dict(),
                headers={"Content-Type": "application/json"},
                timeout=60
            )

            if response.status_code == 200:
                result = response.json()

                # Check for payment details in headers
                payment_response = response.headers.get("x-payment-response")
                if payment_response:
                    print(f"Payment processed: {payment_response}")

                return result.get('response', 'No response content')
            else:
                return f"Error: HTTP {response.status_code} - {response.text}"

        except requests.exceptions.Timeout:
            return "Error: Request timed out. The agent may be processing a complex query."
        except requests.exceptions.ConnectionError:
            return "Error: Could not connect to the agent. It may be offline."
        except Exception as e:
            return f"Error: {str(e)}"


if __name__ == "__main__":

    # Create agent config for the user agent
    # Using separate config_dir so this agent has its own identity
    agent_config = AgentConfig(
        name="User Assistant Agent",
        description="An interactive user assistant that helps with stock research by "
                    "connecting to specialized financial analysis agents.",
        capabilities={
            "ai": ["nlp", "conversational_ai"],
            "protocols": ["http"],
            "services": [
                "user_assistance",
                "agent_orchestration",
                "query_routing"
            ],
            "domains": [
                "general_assistance",
                "finance"
            ]
        },
        webhook_host="0.0.0.0",
        webhook_port=5004,
        auto_reconnect=True,
        message_history_limit=100,
        registry_url="https://registry.zynd.ai",
        api_key=os.environ["ZYND_API_KEY"],
        config_dir=".agent-user"  # Separate identity from other agents
    )

    # Init zynd agent sdk wrapper
    zynd_agent = ZyndAIAgent(agent_config=agent_config)

    # Create the stock comparison client
    stock_client = StockComparisonClient(zynd_agent)

    # Main interactive loop
    print("\n" + "="*60)
    print("User Agent - Stock Research Assistant")
    print("="*60)
    print("\nThis agent will help you research stocks by connecting to")
    print("specialized stock comparison agents in the network.")
    print(f"\nYour payment address: {zynd_agent.pay_to_address}")
    print("(Make sure you have USDC on Base Sepolia for payments)")
    print("\n" + "-"*60)
    print("Commands:")
    print("  search  - Search for stock comparison agents")
    print("  ask     - Ask a stock question (auto-searches if needed)")
    print("  exit    - Quit the program")
    print("-"*60 + "\n")

    connected = False

    while True:
        try:
            user_input = input("\nYou: ").strip()

            if not user_input:
                continue

            if user_input.lower() == "exit":
                print("Goodbye!")
                break

            elif user_input.lower() == "search":
                # Search for stock comparison agents
                agent = stock_client.search_for_stock_agent()
                if agent:
                    connected = stock_client.connect_to_agent(agent)

            elif user_input.lower().startswith("ask "):
                # Extract the question
                question = user_input[4:].strip()
                if not question:
                    print("Please provide a question after 'ask'")
                    continue

                # Auto-search and connect if not already connected
                if not connected:
                    print("Not connected to a stock agent. Searching...")
                    agent = stock_client.search_for_stock_agent()
                    if agent:
                        connected = stock_client.connect_to_agent(agent)
                    else:
                        print("Could not find a stock comparison agent.")
                        continue

                # Ask the question
                response = stock_client.ask_stock_question(question)
                print(f"\nStock Agent Response:\n{'-'*40}")
                print(response)
                print('-'*40)

            else:
                # Treat any other input as a stock question
                # Auto-search and connect if not already connected
                if not connected:
                    print("Searching for a stock comparison agent...")
                    agent = stock_client.search_for_stock_agent()
                    if agent:
                        connected = stock_client.connect_to_agent(agent)
                    else:
                        print("Could not find a stock comparison agent.")
                        print("Please make sure the stock_comparison_agent.py is running.")
                        continue

                # Ask the question
                response = stock_client.ask_stock_question(user_input)
                print(f"\nStock Agent Response:\n{'-'*40}")
                print(response)
                print('-'*40)

        except KeyboardInterrupt:
            print("\n\nInterrupted. Goodbye!")
            break
        except Exception as e:
            print(f"Error: {e}")
