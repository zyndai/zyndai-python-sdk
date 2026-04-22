"""
__AGENT_NAME__ — LangChain Agent on ZyndAI Network

Install dependencies:
    pip install zyndai-agent langchain langchain-openai langchain-community langchain-classic

Run:
    python agent.py
"""

from zyndai_agent.agent import AgentConfig, ZyndAIAgent
from zyndai_agent.message import AgentMessage

from payload import RequestPayload, ResponsePayload, MAX_FILE_SIZE_BYTES
from langchain_openai import ChatOpenAI
from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool
from langchain_community.tools.tavily_search import TavilySearchResults

from dotenv import load_dotenv
import json
import os

load_dotenv()

# Load agent.config.json for runtime settings
_config = {}
if os.path.exists("agent.config.json"):
    with open("agent.config.json") as _f:
        _config = json.load(_f)


@tool
def hello(query: str) -> str:
    """A simple demo tool. Replace with your own tools."""
    return f"Hello! You asked: {query}"


def create_agent():
    llm = ChatOpenAI(model="gpt-3.5-turbo", temperature=0)

    search_tool = TavilySearchResults(max_results=3)
    tools = [hello, search_tool]

    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are __AGENT_NAME__, a helpful AI assistant."),
        MessagesPlaceholder(variable_name="chat_history"),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])

    agent = create_tool_calling_agent(llm, tools, prompt)
    return AgentExecutor(agent=agent, tools=tools, verbose=True)


if __name__ == "__main__":
    agent_config = AgentConfig(
        name=_config.get("name", "__AGENT_NAME__"),
        description=_config.get("description", "__AGENT_NAME__ — a LangChain agent on the ZyndAI network."),
        capabilities={
            "ai": ["nlp", "langchain"],
            "protocols": ["http"],
        },
        category=_config.get("category", "general"),
        tags=_config.get("tags", ["langchain"]),
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
    agent_executor = create_agent()
    zynd_agent.set_langchain_agent(agent_executor)

    def message_handler(message: AgentMessage, topic: str):
        try:
            response = zynd_agent.invoke(message.content, chat_history=[])
            zynd_agent.set_response(message.message_id, response)
        except Exception as e:
            zynd_agent.set_response(message.message_id, f"Error: {str(e)}")

    zynd_agent.add_message_handler(message_handler)

    print(f"\n__AGENT_NAME__ is running (LangChain)")
    print(f"Webhook: {zynd_agent.webhook_url}")
    print("Type 'exit' to quit\n")

    while True:
        cmd = input()
        if cmd.lower() == "exit":
            break
