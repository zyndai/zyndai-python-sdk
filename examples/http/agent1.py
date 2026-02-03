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

    agent_config = AgentConfig(
        name="Agent 1",
        description="A helpful search agent",
        capabilities={
            "ai": ["nlp"],
            "protocols": ["http"]
        },
        webhook_host="0.0.0.0",
        webhook_port=5001,
        auto_reconnect=True,
        message_history_limit=100,
        registry_url="https://registry.zynd.ai",
        price="$0.01",
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
    print("Type 'Exit' to quit\n")

    while True:
        message = input("Message (Exit for exit): ")

        if message == "Exit":
            break

        zynd_agent.send_message(message)
