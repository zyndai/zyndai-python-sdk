"""
User Agent - An intelligent agent that dynamically discovers and communicates with specialized agents.

This agent:
- Takes questions from the terminal (stdio)
- Uses an LLM to analyze queries and extract relevant search terms
- Searches the registry using both keyword and capabilities search
- Ranks and selects the best matching agent for each query
- Automatically retries with other agents if one fails
- Automatically pays the required fee via x402 protocol
- Supports ngrok tunnel for public access

The agent dynamically determines search terms based on user queries,
rather than using hardcoded search terms.

Running multiple agents on the same machine:
    # Terminal 1 - User agent on port 5004
    python examples/http/user_agent.py

    # Terminal 2 - LangChain stock agent on port 5003
    python examples/http/stock_langchain.py

    # Terminal 3 - CrewAI stock agent on port 5011
    python examples/http/stock_crewai.py

    Each agent gets its own ngrok tunnel and public URL automatically.
"""

from zyndai_agent.agent import AgentConfig, ZyndAIAgent
from zyndai_agent.message import AgentMessage
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from pydantic import BaseModel, Field

from dotenv import load_dotenv
import os
import json
import requests
from typing import Optional, List, Tuple

load_dotenv()


class SearchTerms(BaseModel):
    """Extracted search terms from user query."""

    keyword: str = Field(description="Primary keyword for semantic search")
    capabilities: List[str] = Field(
        description="List of capability terms to search for"
    )
    domain: str = Field(
        description="The domain/category of the query (e.g., finance, weather, code)"
    )


class AgentDiscoveryClient:
    """
    Intelligent client for discovering and communicating with specialized agents.
    Uses LLM to extract search terms from user queries dynamically.
    Automatically retries with other agents if one fails.
    """

    def __init__(self, zynd_agent: ZyndAIAgent):
        self.zynd_agent = zynd_agent
        self.connected_agent = None
        self.payment_processor = zynd_agent.x402_processor
        self.llm = ChatOpenAI(model="gpt-3.5-turbo", temperature=0)

        # Store ranked agents for retry
        self.ranked_agents: List[dict] = []
        self.current_agent_index = 0

        # Create the search term extraction chain
        self.search_extractor = self._create_search_extractor()

        # Create the agent ranking chain
        self.agent_ranker = self._create_agent_ranker()

    def _create_search_extractor(self):
        """Create a chain to extract search terms from user queries."""
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    """You are an expert at analyzing user queries and extracting search terms
to find the right AI agent to handle the request.

Given a user query, extract:
1. keyword: A concise keyword phrase for semantic search (2-4 words)
2. capabilities: A list of 3-5 capability terms that describe what kind of agent is needed
3. domain: The primary domain/category (e.g., finance, weather, coding, health, travel)

Think about what skills and expertise an agent would need to answer this query.

Respond with valid JSON only.""",
                ),
                ("human", "Query: {query}"),
            ]
        )

        parser = JsonOutputParser(pydantic_object=SearchTerms)
        return prompt | self.llm | parser

    def _create_agent_ranker(self):
        """Create a chain to rank agents based on relevance to the query."""
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    """You are an expert at matching user queries to AI agents.

Given a user query and a list of available agents, rank them by relevance.
Consider the agent's name, description, and capabilities.

Return a JSON object with:
- "rankings": list of agent indices (0-based) ordered by relevance, best first
- "best_match": index of the single best matching agent
- "confidence": confidence score 0-100 that the best match can handle the query
- "reasoning": brief explanation of why the best match was chosen

If no agents seem relevant, set confidence to 0.

Respond with valid JSON only.""",
                ),
                (
                    "human",
                    """Query: {query}

Available agents:
{agents_info}""",
                ),
            ]
        )

        return prompt | self.llm

    def extract_search_terms(self, query: str) -> dict:
        """Use LLM to extract search terms from the user query."""
        print(f"\nAnalyzing query to determine search terms...")

        try:
            result = self.search_extractor.invoke({"query": query})
            print(f"  Keyword: {result.get('keyword', 'N/A')}")
            print(f"  Capabilities: {result.get('capabilities', [])}")
            print(f"  Domain: {result.get('domain', 'N/A')}")
            return result
        except Exception as e:
            print(f"  Error extracting search terms: {e}")
            # Fallback to using the query itself
            return {
                "keyword": query[:50],
                "capabilities": [query[:30]],
                "domain": "general",
            }

    def search_for_agents(self, query: str) -> List[dict]:
        """
        Search the registry for agents that can handle the query.
        Returns a ranked list of all matching agents.

        Strategy:
        1. First try keyword search (semantic search)
        2. If no results, fall back to capabilities search
        """
        # Step 1: Extract search terms from the query
        search_terms = self.extract_search_terms(query)

        # Step 2: First try keyword search
        print(f"\nSearching registry with keyword: '{search_terms['keyword']}'...")
        agents = self.zynd_agent.search_agents_by_keyword(
            keyword=search_terms["keyword"], limit=10
        )

        # Step 3: If keyword search returned no results, try capabilities search
        if not agents:
            print(f"No results from keyword search. Trying capabilities search...")
            print(f"Searching by capabilities: {search_terms['capabilities']}...")
            agents = self.zynd_agent.search_agents_by_capabilities(
                capabilities=search_terms["capabilities"], top_k=10
            )

        if not agents:
            print("No agents found in the registry matching your query.")
            return []

        print(f"\nFound {len(agents)} agent(s)")

        # Step 4: Rank agents using LLM and return all of them
        ranked_agents = self._rank_agents(query, agents)

        # Store for retry logic
        self.ranked_agents = ranked_agents
        self.current_agent_index = 0

        return ranked_agents

    def _rank_agents(self, query: str, agents: List[dict]) -> List[dict]:
        """Use LLM to rank agents and return them in order of relevance."""
        if len(agents) == 1:
            print(f"\nOnly one agent found: {agents[0].get('name', 'Unknown')}")
            return agents

        # Format agents for the ranking prompt
        agents_info = ""
        for i, agent in enumerate(agents):
            agents_info += f"\n[{i}] {agent.get('name', 'Unknown')}"
            agents_info += f"\n    Description: {agent.get('description', 'N/A')[:150]}"
            agents_info += f"\n    Status: {agent.get('status', 'UNKNOWN')}"
            if agent.get("capabilities"):
                caps = agent.get("capabilities", {})
                agents_info += f"\n    Capabilities: {json.dumps(caps)[:100]}"
            agents_info += "\n"

        print("\nRanking agents by relevance to your query...")

        try:
            result = self.agent_ranker.invoke(
                {"query": query, "agents_info": agents_info}
            )

            # Parse the ranking result
            ranking_text = result.content if hasattr(result, "content") else str(result)

            # Try to extract JSON from the response
            try:
                start = ranking_text.find("{")
                end = ranking_text.rfind("}") + 1
                if start >= 0 and end > start:
                    ranking_data = json.loads(ranking_text[start:end])
                else:
                    ranking_data = {}
            except json.JSONDecodeError:
                ranking_data = {}

            rankings = ranking_data.get("rankings", list(range(len(agents))))
            confidence = ranking_data.get("confidence", 50)
            reasoning = ranking_data.get("reasoning", "Ranked by relevance")

            # Reorder agents based on rankings
            ranked_agents = []
            for idx in rankings:
                if 0 <= idx < len(agents):
                    ranked_agents.append(agents[idx])

            # Add any agents not in rankings
            for agent in agents:
                if agent not in ranked_agents:
                    ranked_agents.append(agent)

            if ranked_agents:
                print(
                    f"\nBest match: {ranked_agents[0].get('name', 'Unknown')} (confidence: {confidence}%)"
                )
                print(f"Reason: {reasoning}")
                if len(ranked_agents) > 1:
                    print(
                        f"Backup agents: {', '.join([a.get('name', 'Unknown') for a in ranked_agents[1:3]])}"
                    )

            return ranked_agents

        except Exception as e:
            print(f"Error ranking agents: {e}")
            return agents

    def connect_to_agent(self, agent: dict) -> bool:
        """Connect to a specific agent."""
        if not agent.get("httpWebhookUrl"):
            print(f"Agent {agent.get('name', 'Unknown')} has no webhook URL.")
            return False

        self.connected_agent = agent
        self.zynd_agent.connect_agent(agent)
        print(f"\nConnected to: {agent.get('name', 'Unknown')}")
        print(f"Webhook: {agent.get('httpWebhookUrl')}")
        return True

    def _try_ask_agent(self, agent: dict, question: str) -> Tuple[bool, str]:
        """
        Try to ask a question to a specific agent.
        Returns (success, response_or_error).
        """
        webhook_url = agent.get("httpWebhookUrl")
        if not webhook_url:
            return False, "No webhook URL"

        sync_url = webhook_url.replace("/webhook", "/webhook/sync")

        try:
            message = AgentMessage(
                content=question,
                sender_id=self.zynd_agent.agent_id,
                receiver_id=agent.get("didIdentifier"),
                message_type="query",
                sender_did=self.zynd_agent.identity_credential,
            )

            response = self.payment_processor.post(
                sync_url,
                json=message.to_dict(),
                headers={"Content-Type": "application/json"},
                timeout=60,
            )

            if response.status_code == 200:
                result = response.json()
                payment_response = response.headers.get("x-payment-response")
                if payment_response:
                    print(f"Payment processed: {payment_response}")
                return True, result.get("response", "No response content")
            else:
                return False, f"HTTP {response.status_code}"

        except requests.exceptions.Timeout:
            return False, "Request timed out"
        except requests.exceptions.ConnectionError:
            return False, "Connection failed"
        except Exception as e:
            return False, str(e)

    def ask_question_with_retry(self, question: str) -> str:
        """
        Ask a question, automatically retrying with other agents if one fails.
        """
        if not self.ranked_agents:
            return "Error: No agents available. Search for agents first."

        # Start from current index
        for i in range(self.current_agent_index, len(self.ranked_agents)):
            agent = self.ranked_agents[i]
            agent_name = agent.get("name", "Unknown")

            print(f"\nTrying agent {i + 1}/{len(self.ranked_agents)}: {agent_name}...")

            # Connect to this agent
            if not self.connect_to_agent(agent):
                print(f"  Failed to connect. Trying next agent...")
                continue

            # Try to ask the question
            success, response = self._try_ask_agent(agent, question)

            if success:
                self.current_agent_index = i  # Remember successful agent
                return response
            else:
                print(f"  Failed: {response}")
                if i < len(self.ranked_agents) - 1:
                    print(f"  Retrying with next agent...")
                else:
                    print(f"  No more agents to try.")

        return "Error: All agents failed to respond. Please try again later."

    def ask_question(self, question: str) -> str:
        """
        Ask a question to the connected agent.
        Automatically handles x402 payment.
        """
        if not self.connected_agent:
            return "Error: Not connected to any agent. Search for an agent first."

        success, response = self._try_ask_agent(self.connected_agent, question)

        if success:
            return response
        else:
            # Try to retry with other agents
            print(f"\nAgent failed: {response}")
            if len(self.ranked_agents) > 1:
                print("Trying other available agents...")
                self.current_agent_index += 1
                return self.ask_question_with_retry(question)
            return f"Error: {response}"

    def process_query(self, query: str) -> str:
        """
        Process a user query end-to-end:
        1. Extract search terms from the query
        2. Search for matching agents
        3. Rank agents by relevance
        4. Try agents in order until one succeeds
        5. Return the response
        """
        # Search for agents that can handle this query
        agents = self.search_for_agents(query)

        if not agents:
            return "No suitable agent found for your query. Please try rephrasing."

        # Try to get a response, with automatic retry
        return self.ask_question_with_retry(query)


if __name__ == "__main__":
    # Create agent config for the user agent with ngrok tunnel
    agent_config = AgentConfig(
        name="Intelligent User Agent",
        description="An intelligent user assistant that dynamically discovers "
        "and communicates with specialized agents based on user queries.",
        capabilities={
            "ai": ["nlp", "conversational_ai", "agent_discovery"],
            "protocols": ["http"],
            "services": [
                "user_assistance",
                "agent_orchestration",
                "query_routing",
                "intelligent_search",
            ],
            "domains": ["general_assistance", "multi_domain"],
        },
        webhook_host="0.0.0.0",
        webhook_port=5004,
        auto_reconnect=True,
        message_history_limit=100,
        registry_url="https://registry.zynd.ai",
        api_key=os.environ["ZYND_API_KEY"],
        config_dir=".agent-user",
        # Enable ngrok to expose this agent publicly (requires: pip install zyndai-agent[ngrok])
        # Each agent on a different port gets its own ngrok tunnel URL
        use_ngrok=True,
        ngrok_auth_token=os.environ.get(
            "NGROK_AUTH_TOKEN"
        ),  # Or set globally via: ngrok config add-authtoken <token>
    )

    # Initialize ZyndAI agent
    zynd_agent = ZyndAIAgent(agent_config=agent_config)

    # Create the intelligent discovery client
    client = AgentDiscoveryClient(zynd_agent)

    # Main interactive loop
    print("\n" + "=" * 60)
    print("Intelligent User Agent")
    print("=" * 60)
    print("\nThis agent will analyze your questions and automatically")
    print("find the best specialized agent to handle them.")
    print("If an agent fails, it will automatically try others.")
    print(f"\nYour payment address: {zynd_agent.pay_to_address}")
    print("(Make sure you have USDC on Base Sepolia for payments)")
    print("\n" + "-" * 60)
    print("Commands:")
    print("  Just type your question - agent will find the right expert")
    print("  'search <query>' - Search for agents without asking")
    print("  'reconnect' - Force search for a new agent")
    print("  'exit' - Quit the program")
    print("-" * 60 + "\n")

    connected = False

    while True:
        try:
            user_input = input("\nYou: ").strip()

            if not user_input:
                continue

            if user_input.lower() == "exit":
                print("Goodbye!")
                break

            elif user_input.lower() == "reconnect":
                connected = False
                client.connected_agent = None
                client.ranked_agents = []
                client.current_agent_index = 0
                print("Disconnected. Next query will search for a new agent.")

            elif user_input.lower().startswith("search "):
                # Search for agents without asking
                query = user_input[7:].strip()
                if not query:
                    print("Please provide a search query after 'search'")
                    continue

                agents = client.search_for_agents(query)
                if agents:
                    connected = client.connect_to_agent(agents[0])
                    print("\nReady to receive questions for this agent.")
                    if len(agents) > 1:
                        print(
                            f"({len(agents) - 1} backup agents available if this one fails)"
                        )

            else:
                # Process the query - either use connected agent or find a new one
                if connected and client.connected_agent:
                    # Ask the already connected agent (with retry if needed)
                    response = client.ask_question(user_input)
                else:
                    # Full process: search, rank, connect, ask with retry
                    response = client.process_query(user_input)
                    connected = client.connected_agent is not None

                print(f"\nAgent Response:\n{'-' * 40}")
                print(response)
                print("-" * 40)

        except KeyboardInterrupt:
            print("\n\nInterrupted. Goodbye!")
            break
        except Exception as e:
            print(f"Error: {e}")
            import traceback

            traceback.print_exc()
