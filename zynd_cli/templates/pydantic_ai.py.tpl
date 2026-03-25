"""
__AGENT_NAME__ — PydanticAI Agent on ZyndAI Network

Install dependencies:
    pip install zyndai-agent pydantic-ai

Run:
    python agent.py
"""

from zyndai_agent.agent import AgentConfig, ZyndAIAgent
from zyndai_agent.message import AgentMessage

from dotenv import load_dotenv
import os

load_dotenv()


def create_agent():
    from pydantic_ai import Agent, RunContext
    from pydantic_ai.models.openai import OpenAIModel

    model = OpenAIModel("gpt-4o-mini")

    agent = Agent(
        model,
        system_prompt="You are __AGENT_NAME__, a helpful AI assistant.",
        result_type=str,
    )

    @agent.tool
    async def search(ctx: RunContext[None], query: str) -> str:
        """Search for information. Replace with your own tools."""
        return f"Search results for '{query}': Demo data — integrate with real APIs."

    return agent


if __name__ == "__main__":
    agent_config = AgentConfig(
        name="__AGENT_NAME__",
        description="__AGENT_NAME__ — a PydanticAI agent on the ZyndAI network.",
        capabilities={
            "ai": ["nlp", "pydantic_ai"],
            "protocols": ["http"],
        },
        category="general",
        tags=["pydantic-ai"],
        summary="__AGENT_NAME__ agent",
        webhook_host="0.0.0.0",
        webhook_port=5000,
        registry_url=os.environ.get("ZYND_REGISTRY_URL", "http://localhost:8080"),
        auto_register=True,
    )

    zynd_agent = ZyndAIAgent(agent_config=agent_config)
    pydantic_agent = create_agent()
    zynd_agent.set_pydantic_ai_agent(pydantic_agent)

    def message_handler(message: AgentMessage, topic: str):
        try:
            response = zynd_agent.invoke(message.content)
            zynd_agent.set_response(message.message_id, response)
        except Exception as e:
            zynd_agent.set_response(message.message_id, f"Error: {str(e)}")

    zynd_agent.add_message_handler(message_handler)

    print(f"\n__AGENT_NAME__ is running (PydanticAI)")
    print(f"Webhook: {zynd_agent.webhook_url}")
    print("Type 'exit' to quit\n")

    while True:
        cmd = input()
        if cmd.lower() == "exit":
            break
