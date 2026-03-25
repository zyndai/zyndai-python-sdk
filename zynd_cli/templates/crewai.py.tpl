"""
__AGENT_NAME__ — CrewAI Agent on ZyndAI Network

Install dependencies:
    pip install zyndai-agent crewai crewai-tools

Run:
    python agent.py
"""

from zyndai_agent.agent import AgentConfig, ZyndAIAgent
from zyndai_agent.message import AgentMessage

from dotenv import load_dotenv
import os

load_dotenv()


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
        name="__AGENT_NAME__",
        description="__AGENT_NAME__ — a CrewAI multi-agent system on the ZyndAI network.",
        capabilities={
            "ai": ["nlp", "crewai", "multi_agent"],
            "protocols": ["http"],
        },
        category="general",
        tags=["crewai", "multi-agent"],
        summary="__AGENT_NAME__ agent",
        webhook_host="0.0.0.0",
        webhook_port=5000,
        registry_url=os.environ.get("ZYND_REGISTRY_URL", "http://localhost:8080"),
        auto_register=True,
    )

    zynd_agent = ZyndAIAgent(agent_config=agent_config)
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
