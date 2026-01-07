from zyndai_agent.agent import AgentConfig, ZyndAIAgent
from langchain_openai import ChatOpenAI
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
        Zynd AI agent registry url
    mqtt_broker_url:
        default mqtt broker url on which you will be listening on
    identity_credential_path:
        file path of credential document of the agent downloaded from the Zynd AI dashboard
    secret_seed:
        Seed string of agent downloaded from the Zynd AI dashboard
    """
    agent_config = AgentConfig(
        default_outbox_topic=None,
        auto_reconnect=True,
        message_history_limit=100,
        registry_url="https://registry.zynd.ai",
        mqtt_broker_url="mqtt://registry.zynd.ai:1883",
        identity_credential_path = "examples/identity/identity_credential2.json",
        secret_seed = os.environ["AGENT2_SEED"]
    )


    # Init zynd agent sdk wrapper
    zynd_agent = ZyndAIAgent(agent_config=agent_config)

    # Created a langchain LLM (not using memory in this simple example)
    llm = ChatOpenAI(model="gpt-3.5-turbo", temperature=0)

    zynd_agent.set_agent_executor(llm)


    while True:
        search_filter = input("Search Agent: ")
        agents = zynd_agent.search_agents_by_capabilities([search_filter])

        print("Agents Found")
        for agent in agents:
            print(f"""
                DID: {agent["didIdentifier"]}
                Description: {agent["description"]}
                Match Score: {agent["matchScore"]}
            """)
            print("================")

        agent_select = input("Connect to agent DID: ")

        selected_agent = None
        for agent in agents:
            if agent["didIdentifier"] == agent_select:
                selected_agent = agent

        if not selected_agent:
            raise "Invalid did agent not found"

        zynd_agent.connect_agent(selected_agent)

        print("Connected to agent")

        while True:
            message = input("Message (Exit for exit): ")

            if message == "Exit":
                break

            zynd_agent.send_message(message)
