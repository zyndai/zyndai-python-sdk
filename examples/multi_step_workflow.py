"""
Multi-step orchestration workflow: research -> implement -> verify.

Demonstrates sequential phases where each phase uses parallel fan_out,
and later phases depend on earlier results. The coordinator synthesizes
everything into a final deliverable.
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
    name="project-manager",
    description="Orchestrates multi-step projects across specialist agents",
    capabilities={"skills": ["project-management", "orchestration"]},
    category="orchestration",
    webhook_port=5030,
    registry_url=os.getenv("REGISTRY_URL", "https://registry.zynd.ai"),
))

coordinator = Coordinator(
    agent=agent,
    max_concurrent=5,
    default_timeout=120.0,
    default_budget_usd=1.00,
)


@coordinator.strategy("build-feature")
async def build_feature(description: str, ctx: OrchestrationContext):
    # Phase 1: Research — gather context in parallel
    research = await ctx.fan_out([
        ("codebase-analyzer", f"Analyze existing codebase for: {description}"),
        ("docs-searcher", f"Find documentation related to: {description}"),
    ])
    research_summary = ctx.synthesize(research)

    # Phase 2: Implement — use synthesized briefing (not raw JSON) to guide implementation
    implementation = await ctx.call_agent(
        "coder",
        f"Implement the following feature using this context:\n"
        f"Feature: {description}\n\n"
        f"{research_summary.get('briefing', str(research_summary['results']))}",
    )

    # Phase 3: Verify — fresh agent reviews the implementation
    if implementation.status == "success":
        verification = await ctx.call_agent(
            "code-reviewer",
            f"Review this implementation for correctness and security:\n"
            f"{implementation.result}",
        )
    else:
        verification = None

    return {
        "feature": description,
        "research": research_summary,
        "implementation": implementation.result if implementation.status == "success" else None,
        "implementation_error": implementation.error if implementation.status != "success" else None,
        "verification": verification.result if verification and verification.status == "success" else None,
        "total_cost": ctx.budget_usd - ctx.budget_remaining,
        "task_summary": ctx.task_tracker.summary(),
    }


def handle_message(message, topic, session):
    """Handle incoming project requests."""
    result = coordinator.execute_sync("build-feature", message.content)
    agent.set_response(message.message_id, str(result))


agent.register_handler(handle_message)

print("\nProject manager running.")
print("Registered strategy: build-feature (research -> implement -> verify)")
print("Press Ctrl+C to stop.\n")

try:
    import time
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\nShutting down.")
