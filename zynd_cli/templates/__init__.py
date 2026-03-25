"""Agent project templates for `zynd agent init`."""

FRAMEWORKS = {
    "langchain": {
        "label": "LangChain",
        "description": "Tool-calling agents with memory and search",
        "install": "pip install langchain langchain-openai langchain-community langchain-classic",
        "env_keys": ["OPENAI_API_KEY", "TAVILY_API_KEY"],
    },
    "langgraph": {
        "label": "LangGraph",
        "description": "Graph-based agent with explicit state management",
        "install": "pip install langchain-openai langchain-community langgraph",
        "env_keys": ["OPENAI_API_KEY", "TAVILY_API_KEY"],
    },
    "crewai": {
        "label": "CrewAI",
        "description": "Multi-agent collaboration (researcher + analyst)",
        "install": "pip install crewai crewai-tools",
        "env_keys": ["OPENAI_API_KEY", "SERPER_API_KEY"],
    },
    "pydantic-ai": {
        "label": "PydanticAI",
        "description": "Type-safe agents with structured outputs",
        "install": "pip install pydantic-ai",
        "env_keys": ["OPENAI_API_KEY"],
    },
    "custom": {
        "label": "Custom",
        "description": "Minimal template — bring your own framework",
        "install": "pip install zyndai-agent",
        "env_keys": [],
    },
}

FRAMEWORK_ORDER = ["langchain", "langgraph", "crewai", "pydantic-ai", "custom"]
