"""
Coordinator agent with fan-out research strategy.

Discovers specialist agents via the registry, dispatches tasks in parallel
using fan_out, and synthesizes results into a coherent response.
"""

import os
from dotenv import load_dotenv

load_dotenv()

from zyndai_agent import (
    ZyndAIAgent,
    AgentConfig,
    Coordinator,
    OrchestrationContext,
)

agent = ZyndAIAgent(AgentConfig(
    name="research-coordinator",
    description="Coordinates research across multiple specialist agents",
    capabilities={"skills": ["research", "analysis", "coordination"]},
    category="orchestration",
    webhook_port=5020,
    registry_url=os.getenv("REGISTRY_URL", "https://registry.zynd.ai"),
))

coordinator = Coordinator(
    agent=agent,
    max_concurrent=5,
    default_timeout=60.0,
    default_budget_usd=0.50,
)


@coordinator.strategy("deep-research")
async def research(topic: str, ctx: OrchestrationContext):
    # Phase 1: Fan out to 3 specialist agents in parallel
    research_results = await ctx.fan_out([
        ("web-search", f"Find recent papers and articles about {topic}"),
        ("data-analysis", f"Find relevant datasets and statistics about {topic}"),
        ("expert-finder", f"Find domain experts who've published on {topic}"),
    ])

    # Phase 2: Synthesize with a summarizer agent
    summary_input = ctx.synthesize(research_results)
    if summary_input["status"] == "success":
        summary = await ctx.call_agent(
            "summarizer",
            f"Synthesize these research findings: {summary_input['results']}",
        )
    else:
        summary = None

    return {
        "research": summary_input,
        "summary": summary.result if summary and summary.status == "success" else None,
        "total_cost": ctx.budget_usd - ctx.budget_remaining,
    }


def handle_message(message, topic, session):
    """Handle incoming research requests via the coordinator."""
    result = coordinator.execute_sync("deep-research", message.content)
    agent.set_response(message.message_id, str(result))


agent.register_handler(handle_message)

print("\nResearch coordinator running.")
print("Registered strategy: deep-research")
print("Press Ctrl+C to stop.\n")

try:
    import time
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\nShutting down.")
