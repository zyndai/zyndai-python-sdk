"""
__AGENT_NAME__ — CrewAI Agent on ZyndAI Network

Install dependencies:
    pip install zyndai-agent crewai crewai-tools

Run:
    python agent.py
"""

from zyndai_agent.agent import AgentConfig, ZyndAIAgent
from zyndai_agent.message import AgentMessage

from payload import RequestPayload, ResponsePayload, MAX_FILE_SIZE_BYTES

from dotenv import load_dotenv
import json
import os

load_dotenv()

# Load agent.config.json for runtime settings
_config = {}
if os.path.exists("agent.config.json"):
    with open("agent.config.json") as _f:
        _config = json.load(_f)


def create_crew():
    from crewai import Agent, Task, Crew, Process
    from crewai_tools import SerperDevTool

    search_tool = SerperDevTool()

    researcher = Agent(
        role="Researcher",
        goal="Research and gather comprehensive data",
        backstory="You are an expert researcher who excels at finding relevant information.",
        tools=[search_tool],
        verbose=True,
    )

    analyst = Agent(
        role="Analyst",
        goal="Analyze data and provide insights",
        backstory="You are a senior analyst who provides balanced, professional analysis.",
        verbose=True,
    )

    research_task = Task(
        description="Research the topic: {query}. Gather key data and facts.",
        expected_output="Comprehensive research data",
        agent=researcher,
    )

    analysis_task = Task(
        description="Analyze the research and provide insights on: {query}",
        expected_output="Professional analysis with key takeaways",
        agent=analyst,
    )

    return Crew(
        agents=[researcher, analyst],
        tasks=[research_task, analysis_task],
        process=Process.sequential,
        verbose=True,
    )


if __name__ == "__main__":
    agent_config = AgentConfig(
        name=_config.get("name", "__AGENT_NAME__"),
        description=_config.get("description", "__AGENT_NAME__ — a CrewAI multi-agent system on the ZyndAI network."),
        capabilities={
            "ai": ["nlp", "crewai", "multi_agent"],
            "protocols": ["http"],
        },
        category=_config.get("category", "general"),
        tags=_config.get("tags", ["crewai", "multi-agent"]),
        summary=_config.get("summary", "__AGENT_NAME__ agent"),
        webhook_host="0.0.0.0",
        webhook_port=_config.get("webhook_port", 5000),
        registry_url=os.environ.get("ZYND_REGISTRY_URL", _config.get("registry_url", "http://localhost:8080")),
        keypair_path=os.environ.get("ZYND_AGENT_KEYPAIR_PATH", _config.get("keypair_path")),
        entity_url=os.environ.get("ZYND_ENTITY_URL", _config.get("entity_url")),
        price=_config.get("price"),
        entity_pricing=_config.get("entity_pricing"),
    )

    zynd_agent = ZyndAIAgent(
        agent_config=agent_config,
        payload_model=RequestPayload,
        output_model=ResponsePayload,
        max_file_size_bytes=MAX_FILE_SIZE_BYTES,
    )
    crew = create_crew()
    zynd_agent.set_crewai_agent(crew)

    def message_handler(message: AgentMessage, topic: str):
        try:
            response = zynd_agent.invoke(message.content)
            zynd_agent.set_response(message.message_id, response)
        except Exception as e:
            zynd_agent.set_response(message.message_id, f"Error: {str(e)}")

    zynd_agent.add_message_handler(message_handler)

    print(f"\n__AGENT_NAME__ is running (CrewAI)")
    print(f"Webhook: {zynd_agent.webhook_url}")
    print("Type 'exit' to quit\n")

    while True:
        cmd = input()
        if cmd.lower() == "exit":
            break
