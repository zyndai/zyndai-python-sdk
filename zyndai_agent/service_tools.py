"""
Dynamic service tool generation for AI agents.

Converts registered services into callable tools that any LLM agent
framework can use. The pipeline:

1. search_services() finds top matching services (pgvector semantic search)
2. Service specs are converted to tool schemas (function-calling format)
3. LLM picks the right tool and generates params
4. Tool executor calls the service endpoint

Works with LangChain, CrewAI, PydanticAI, and raw function-calling APIs.
"""

import json
import logging
import requests
from typing import Any, Dict, List, Optional, Callable

logger = logging.getLogger(__name__)

# Minimal OpenAPI-style specs for each gateway service
# These describe the actual API shape so the LLM can generate correct params
SERVICE_SPECS: Dict[str, Dict[str, Any]] = {
    "weather": {
        "name": "zynd_weather",
        "description": "Get weather forecast for any location worldwide. Returns current temperature, wind speed, and forecast data.",
        "parameters": {
            "latitude": {"type": "number", "description": "Latitude of the location (e.g., 37.77 for San Francisco, 35.68 for Tokyo)"},
            "longitude": {"type": "number", "description": "Longitude of the location (e.g., -122.42 for San Francisco, 139.69 for Tokyo)"},
        },
        "required": ["latitude", "longitude"],
        "method": "GET",
        "path": "",
    },
    "crypto": {
        "name": "zynd_crypto_prices",
        "description": "Get real-time cryptocurrency prices. Supports 10,000+ tokens including Bitcoin, Ethereum, Solana, etc.",
        "parameters": {
            "ids": {"type": "string", "description": "Comma-separated coin IDs (e.g., 'bitcoin,ethereum,solana')"},
            "vs_currencies": {"type": "string", "description": "Target currency (default: 'usd')"},
        },
        "required": ["ids"],
        "method": "GET",
        "path": "/price",
    },
    "wikipedia": {
        "name": "zynd_wikipedia",
        "description": "Search Wikipedia or get article summaries. Use for general knowledge lookups about any topic.",
        "parameters": {
            "title": {"type": "string", "description": "Article title to get summary for (e.g., 'Ethereum', 'Tokyo', 'Machine learning')"},
        },
        "required": ["title"],
        "method": "GET",
        "path": "/summary",
    },
    "translate": {
        "name": "zynd_translate",
        "description": "Translate text between 30+ languages.",
        "parameters": {
            "text": {"type": "string", "description": "Text to translate"},
            "source": {"type": "string", "description": "Source language code (e.g., 'en', 'es', 'fr', 'de', 'ja')"},
            "target": {"type": "string", "description": "Target language code"},
        },
        "required": ["text", "target"],
        "method": "POST",
        "path": "",
    },
    "exchange": {
        "name": "zynd_exchange_rates",
        "description": "Get currency exchange rates for 170+ currencies.",
        "parameters": {
            "from": {"type": "string", "description": "Base currency code (e.g., 'USD', 'EUR', 'GBP')"},
            "to": {"type": "string", "description": "Target currency codes, comma-separated (e.g., 'EUR,JPY,GBP')"},
        },
        "required": ["from"],
        "method": "GET",
        "path": "/rate",
    },
    "news": {
        "name": "zynd_tech_news",
        "description": "Get latest tech news from Hacker News. Returns top, new, or best stories.",
        "parameters": {
            "type": {"type": "string", "description": "Story type: 'top', 'new', 'best', 'ask', 'show', 'job'"},
            "limit": {"type": "integer", "description": "Number of stories to return (max 30)"},
        },
        "required": [],
        "method": "GET",
        "path": "/hn",
    },
    "geocode": {
        "name": "zynd_geocode",
        "description": "Convert addresses to coordinates (geocoding) or coordinates to addresses (reverse geocoding).",
        "parameters": {
            "address": {"type": "string", "description": "Address or place name to geocode (e.g., 'Eiffel Tower Paris', '1600 Pennsylvania Ave')"},
        },
        "required": ["address"],
        "method": "GET",
        "path": "/geocode",
    },
    "defi": {
        "name": "zynd_defi_data",
        "description": "Get DeFi protocol data: TVL (Total Value Locked), yield rates, and chain data from DeFi Llama.",
        "parameters": {
            "protocol": {"type": "string", "description": "Protocol name (e.g., 'aave', 'uniswap', 'lido'). Omit for top protocols."},
        },
        "required": [],
        "method": "GET",
        "path": "/tvl",
    },
    "countries": {
        "name": "zynd_countries",
        "description": "Get country data: population, capital, languages, currencies, borders, flags.",
        "parameters": {
            "name": {"type": "string", "description": "Country name to search for (e.g., 'Japan', 'Brazil')"},
        },
        "required": ["name"],
        "method": "GET",
        "path": "/search",
    },
    "books": {
        "name": "zynd_books",
        "description": "Search millions of books by title, author, or subject via Open Library.",
        "parameters": {
            "query": {"type": "string", "description": "Search query (title, author, or subject)"},
        },
        "required": ["query"],
        "method": "GET",
        "path": "/search",
    },
    "wikidata": {
        "name": "zynd_wikidata",
        "description": "Search the Wikidata knowledge graph for structured entity data.",
        "parameters": {
            "query": {"type": "string", "description": "Entity to search for"},
        },
        "required": ["query"],
        "method": "GET",
        "path": "/search",
    },
    "readability": {
        "name": "zynd_extract_article",
        "description": "Extract clean article content from any URL. Strips ads, navigation, and clutter.",
        "parameters": {
            "url": {"type": "string", "description": "URL of the article to extract"},
        },
        "required": ["url"],
        "method": "POST",
        "path": "/extract",
    },
    "duckduckgo": {
        "name": "zynd_instant_search",
        "description": "Get instant answers and knowledge graph data from DuckDuckGo.",
        "parameters": {
            "query": {"type": "string", "description": "Search query"},
        },
        "required": ["query"],
        "method": "GET",
        "path": "",
    },
}


def get_service_tools(
    registry_url: str = "http://localhost:8080",
    keyword: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """
    Get tool definitions for services matching a query.

    Returns OpenAI function-calling format tools that can be passed to any LLM.
    Uses semantic search to find the most relevant services (pgvector in AgentDNS).

    Args:
        registry_url: AgentDNS URL
        keyword: Search keyword to filter services
        category: Category filter
        limit: Max tools to return (keep at 3-7 for best LLM accuracy)

    Returns:
        List of tool definitions in OpenAI function-calling format
    """
    from zyndai_agent import dns_registry

    result = dns_registry.search_agents(
        registry_url=registry_url,
        query=keyword or "service",
        entity_type="service",
        max_results=limit,
    )

    tools = []
    for svc in result.get("results", []):
        slug = _extract_slug(svc)
        spec = SERVICE_SPECS.get(slug)
        if not spec:
            # Generate a generic tool for unknown services
            spec = _generic_spec(svc)

        tools.append({
            "type": "function",
            "function": {
                "name": spec["name"],
                "description": spec["description"],
                "parameters": {
                    "type": "object",
                    "properties": spec["parameters"],
                    "required": spec.get("required", []),
                },
            },
            "_zynd_meta": {
                "service_id": svc.get("agent_id"),
                "service_endpoint": svc.get("service_endpoint"),
                "slug": slug,
                "method": spec.get("method", "GET"),
                "path": spec.get("path", ""),
            },
        })

    return tools


def execute_service_tool(
    tool_name: str,
    tool_args: Dict[str, Any],
    tools: List[Dict[str, Any]],
    x402_session: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Execute a tool call against the actual service.

    Args:
        tool_name: The function name the LLM chose
        tool_args: The arguments the LLM generated
        tools: The tool list from get_service_tools() (contains _zynd_meta)
        x402_session: Optional x402 requests session for paid services

    Returns:
        The service's JSON response
    """
    tool_def = next((t for t in tools if t["function"]["name"] == tool_name), None)
    if not tool_def:
        raise ValueError(f"Unknown tool: {tool_name}")

    meta = tool_def["_zynd_meta"]
    endpoint = meta["service_endpoint"]
    method = meta["method"]
    path = meta.get("path", "")

    url = endpoint.rstrip("/") + path
    http = x402_session.session if x402_session and hasattr(x402_session, "session") else requests

    if method.upper() == "POST":
        resp = http.post(url, json=tool_args, timeout=30)
    else:
        resp = http.get(url, params=tool_args, timeout=30)

    if resp.status_code >= 400:
        raise RuntimeError(f"Service returned {resp.status_code}: {resp.text[:200]}")

    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text}


def get_langchain_tools(
    registry_url: str = "http://localhost:8080",
    keyword: Optional[str] = None,
    limit: int = 5,
) -> list:
    """
    Get LangChain Tool objects for services matching a query.

    Returns ready-to-use LangChain tools that can be passed directly to
    create_react_agent, AgentExecutor, or any LangChain agent.

    Requires: pip install langchain-core
    """
    from langchain_core.tools import StructuredTool
    from pydantic import create_model, Field

    tool_defs = get_service_tools(registry_url=registry_url, keyword=keyword, limit=limit)
    lc_tools = []

    for tool_def in tool_defs:
        fn = tool_def["function"]
        meta = tool_def["_zynd_meta"]

        # Build Pydantic model for args
        fields = {}
        for param_name, param_spec in fn["parameters"].get("properties", {}).items():
            ptype = str if param_spec.get("type") == "string" else (
                float if param_spec.get("type") == "number" else (
                    int if param_spec.get("type") == "integer" else str
                )
            )
            is_required = param_name in fn["parameters"].get("required", [])
            if is_required:
                fields[param_name] = (ptype, Field(description=param_spec.get("description", "")))
            else:
                fields[param_name] = (Optional[ptype], Field(default=None, description=param_spec.get("description", "")))

        ArgsModel = create_model(f"{fn['name']}_args", **fields)

        # Closure to capture meta
        def make_fn(m, all_tools):
            def service_fn(**kwargs) -> str:
                result = execute_service_tool(m["name"], kwargs, all_tools)
                return json.dumps(result, indent=2, default=str)[:10000]
            return service_fn

        tool = StructuredTool(
            name=fn["name"],
            description=fn["description"],
            func=make_fn({"name": fn["name"]}, tool_defs),
            args_schema=ArgsModel,
        )
        lc_tools.append(tool)

    return lc_tools


def _extract_slug(service: Dict[str, Any]) -> str:
    """Extract the slug from a service's endpoint URL."""
    endpoint = service.get("service_endpoint", "")
    if "/v1/" in endpoint:
        return endpoint.split("/v1/")[-1].split("/")[0].split("?")[0]
    name = service.get("name", "").lower()
    for slug in SERVICE_SPECS:
        if slug in name:
            return slug
    return ""


def _generic_spec(service: Dict[str, Any]) -> Dict[str, Any]:
    """Generate a generic tool spec for a service without a known spec."""
    name = service.get("name", "unknown").lower().replace(" ", "_").replace("-", "_")
    return {
        "name": f"zynd_{name}",
        "description": service.get("summary", service.get("name", "")),
        "parameters": {
            "query": {"type": "string", "description": "Query or input for the service"},
        },
        "required": ["query"],
        "method": "GET",
        "path": "",
    }
