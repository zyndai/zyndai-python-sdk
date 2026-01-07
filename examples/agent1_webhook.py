from zyndai_agent.agent import AgentConfig, ZyndAIAgent
from zyndai_agent.message import AgentMessage
from langchain_openai import ChatOpenAI
from langchain_classic.memory import ChatMessageHistory
from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_community.tools.tavily_search import TavilySearchResults

from dotenv import load_dotenv
import os
from time import sleep

load_dotenv()


if __name__ == "__main__":

    # Create agent config with webhook settings

    """
    Webhook Configuration:
        webhook_host:
            Host address to bind the webhook server (0.0.0.0 allows external connections)
        webhook_port:
            Port number for the webhook server (5000 for agent1)
        webhook_url:
            Optional public URL if behind NAT/proxy (auto-generated if None)
        auto_reconnect:
            Auto-restart the webhook server if it fails
        message_history_limit:
            Store <limit> number of past messages for better context
        registry_url:
            Zynd AI agent registry URL
        identity_credential_path:
            File path of credential document of the agent downloaded from the Zynd AI dashboard
        secret_seed:
            Seed string of agent downloaded from the Zynd AI dashboard
    """
    agent_config = AgentConfig(
        webhook_host="0.0.0.0",
        webhook_port=5001,  # Agent 1 uses port 5001
        webhook_url=None,  # Will auto-generate http://localhost:5001/webhook
        auto_reconnect=True,
        message_history_limit=100,
        registry_url="https://registry.zynd.ai",
        identity_credential_path="examples/identity/identity_credential1.json",
        secret_seed=os.environ["AGENT1_SEED"],
        agent_id=os.environ["AGENT1_ID"],
        price="$0.01",
        pay_to_address="0xc5148b96d3F6T3234721C72EC8a57a4B07A45ca9",
        api_key=os.environ["ZYND_API_KEY"]
    )

    # Init zynd agent sdk wrapper
    zynd_agent = ZyndAIAgent(agent_config=agent_config)

    # Create a langchain agent
    llm = ChatOpenAI(model="gpt-3.5-turbo", temperature=0)
    search_tool = TavilySearchResults(max_results=3)

    # Create message history store
    message_history = ChatMessageHistory()

    # Create prompt template
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a helpful AI agent. Use search when the user asks anything about current events, facts, or the web."),
        MessagesPlaceholder(variable_name="chat_history"),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad")
    ])

    # Create agent with tool calling
    agent = create_tool_calling_agent(llm, [search_tool], prompt)
    agent_executor = AgentExecutor(agent=agent, tools=[search_tool], verbose=True)

    zynd_agent.set_agent_executor(agent_executor)


    def message_handler(message: AgentMessage, topic: str):
        # Add user message to history
        message_history.add_user_message(message.content)

        agent_response = zynd_agent.agent_executor.invoke({
            "input": message.content,
            "chat_history": message_history.messages
        })
        agent_output = agent_response["output"]

        # Add AI response to history
        message_history.add_ai_message(agent_output)

        # Set the response for synchronous mode
        zynd_agent.set_response(message.message_id, agent_output)

        # Also send via webhook if target is connected (for agent-to-agent communication)
        if zynd_agent.target_webhook_url:
            zynd_agent.send_message(agent_output)

    zynd_agent.add_message_handler(message_handler)


    # Main loop
    print("\nWebhook Agent 1 is running!")
    print(f"Webhook URL: {zynd_agent.webhook_url}")
    print("Type 'Exit' to quit\n")

    while True:
        message = input("Message (Exit for exit): ")

        if message == "Exit":
            break

        zynd_agent.send_message(message)
