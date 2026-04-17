#!/usr/bin/env python3
"""
Researcher Agent with AG-UI Streaming.

Streams live research results with citations as tool calls.
Demonstrates tool streaming and STATE updates.

Usage:
    python researcher_agent.py

Then call it with a query - it will stream tool calls and citations.
"""

import asyncio
import logging
import requests
from typing import Any
from zyndai_agent import ZyndAIAgent, AgentConfig

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def search_hector_rag(query: str, top_k: int = 3) -> list[dict]:
    """
    Search hector-rag for relevant documents.
    Falls back to mock data if service unavailable.
    """
    try:
        # Try to connect to hector-rag
        response = requests.post(
            "http://localhost:8000/search",
            json={"query": query, "top_k": top_k},
            timeout=10,
        )
        if response.status_code == 200:
            return response.json().get("results", [])
    except Exception as e:
        logger.warning(f"Could not reach hector-rag: {e}")

    # Mock fallback data
    return [
        {
            "document": f"Mock result about '{query}' from research database #{i}",
            "score": 0.95 - (i * 0.1),
            "source": f"https://example.com/article-{i}",
            "title": f"Understanding {query} - Part {i+1}",
        }
        for i in range(top_k)
    ]


async def main():
    """Run researcher agent with AG-UI streaming."""

    config = AgentConfig(
        name="Researcher",
        description="Real-time research with live citations and tool calls",
        webhook_host="0.0.0.0",
        webhook_port=5001,
        generative_ui=True,  # Enable AG-UI streaming
        registry_url="http://localhost:8080",
    )

    agent = ZyndAIAgent(agent_config=config)

    @agent.register_handler
    async def handle_research(message, ui):
        """Handle research query and stream results."""

        query = message.content.strip()
        if not query or len(query) < 3:
            await ui.text("Please provide a research query")
            return "Error: Query too short"

        # Emit start
        await ui.text(f"🔍 Researching: {query}")

        # Stream tool call
        tool_use_id = "search-hector-1"
        await ui.tool_call(
            "search_hector_rag",
            {"query": query, "top_k": 5},
            tool_use_id=tool_use_id,
        )

        await ui.text("Searching knowledge base...")

        # Perform search
        results = await search_hector_rag(query, top_k=5)

        # Stream tool result
        await ui.tool_result(tool_use_id, f"Found {len(results)} relevant sources")

        # Stream each citation as it's processed
        citations = []
        for idx, result in enumerate(results, 1):
            await ui.text(
                f"\n**Citation {idx}**: {result.get('title', 'Untitled')}\n"
                f"Score: {result.get('score', 0):.2%}\n"
                f"Source: {result.get('source', 'Unknown')}"
            )

            citations.append({
                "id": idx,
                "title": result.get("title", "Untitled"),
                "source": result.get("source", ""),
                "score": result.get("score", 0),
                "document": result.get("document", ""),
            })

            # Small delay to simulate streaming
            await asyncio.sleep(0.3)

        # Stream final state
        await ui.state_snapshot({
            "query": query,
            "citations_count": len(citations),
            "citations": citations,
            "status": "complete",
        })

        await ui.text(
            f"\n✅ Research complete: Found {len(citations)} relevant sources"
        )

        return f"Research results for '{query}' with {len(citations)} citations"

    # Wait indefinitely
    print("\n✅ Researcher Agent running")
    print(f"📍 Webhook: http://localhost:5001/webhook")
    print(f"📡 Stream test: http://localhost:5001/ui/stream/test-research-1")
    print(f"Try: curl -X POST http://localhost:5001/webhook/sync -H 'Content-Type: application/json' -d '{{\"content\": \"quantum computing\", \"sender_id\": \"test\", \"conversation_id\": \"test-research-1\"}}'\n")

    try:
        await asyncio.sleep(float('inf'))
    except KeyboardInterrupt:
        print("\n⛔ Shutting down...")
        agent.stop_webhook_server()


if __name__ == "__main__":
    asyncio.run(main())
