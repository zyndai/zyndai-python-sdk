from zyndai_agent.agent import AgentConfig, ZyndAIAgent
from zyndai_agent.communication import MQTTMessage
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

    # Create agent config


    """
    default_outbox_topic:
        <agent_id>/inbox is used to connect to other agents topic and communicate with it
    auto_reconnect:
        auto run the connection logic if disconnection happens
    message_history:
        store <limit> number of past messages for better context
    registry_url:
        P3 AI agent registry url
    mqtt_broker_url:
        default mqtt broker url on which you will be listening on
    identity_credential_path:
        file path of credential document of the agent downloaded from the P3 AI dashboard 
    secret_seed:
        Seed string of agent downloaded from the P3 AI dashboard
    """
    agent_config = AgentConfig(
        default_outbox_topic=None,
        auto_reconnect=True,
        message_history_limit=100,
        registry_url="https://registry.zynd.ai",
        mqtt_broker_url="mqtt://registry.zynd.ai:1883",
        identity_credential_path = "zynd-agent/examples/identity_credential1.json",
        secret_seed = os.environ["AGENT1_SEED"]
    )


    # Init p3 agent sdk wrapper
    p3_agent = ZyndAIAgent(agent_config=agent_config)

    # Created a langchain agent
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

    p3_agent.set_agent_executor(agent_executor)


    def message_handler(message: MQTTMessage, topic: str):
        # Add user message to history
        message_history.add_user_message(message.content)

        agent_response = p3_agent.agent_executor.invoke({
            "input": message.content,
            "chat_history": message_history.messages
        })
        agent_output = agent_response["output"]

        # Add AI response to history
        message_history.add_ai_message(agent_output)

        p3_agent.send_message(agent_output)

    p3_agent.add_message_handler(message_handler)


    # Main loop
    while True:
        message = input("Message (Exit for exit): ")

        if message == "Exit":
            break
        
        p3_agent.send_message(message)
    