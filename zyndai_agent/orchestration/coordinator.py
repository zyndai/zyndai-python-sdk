"""
Strategy-based orchestration coordinator.

Coordinator lets developers define named strategies (async functions decorated
with @coordinator.strategy) that orchestrate multiple agents via fan_out,
call_agent, and synthesize patterns.

Usage:
    coordinator = Coordinator(agent=my_agent)

    @coordinator.strategy("research")
    async def research(topic: str, ctx: OrchestrationContext):
        results = await ctx.fan_out([
            ("web-search", f"search for {topic}"),
            ("summarizer", f"summarize findings on {topic}"),
        ])
        return ctx.synthesize(results)

    result = await coordinator.execute("research", "quantum computing")
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from zyndai_agent.orchestration.task import TaskTracker
from zyndai_agent.orchestration.fan_out import fan_out, FanOutResult
from zyndai_agent.session import AgentSession
from zyndai_agent.typed_messages import InvokeMessage, generate_id

logger = logging.getLogger(__name__)

StrategyFn = Callable[..., Awaitable[dict[str, Any]]]


class Coordinator:
    """Orchestrates multiple agents via registered strategies."""

    def __init__(
        self,
        agent: Any,
        max_concurrent: int = 10,
        default_timeout: float = 60.0,
        default_budget_usd: float = 1.0,
    ):
        self.agent = agent
        self.max_concurrent = max_concurrent
        self.default_timeout = default_timeout
        self.default_budget_usd = default_budget_usd
        self._strategies: dict[str, StrategyFn] = {}

    def strategy(self, name: str) -> Callable[[StrategyFn], StrategyFn]:
        """Decorator to register a named orchestration strategy."""
        def decorator(func: StrategyFn) -> StrategyFn:
            self._strategies[name] = func
            return func
        return decorator

    async def execute(
        self,
        strategy_name: str,
        task_description: str,
        *,
        budget_usd: float | None = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Run a registered strategy."""
        if strategy_name not in self._strategies:
            raise ValueError(
                f"Unknown strategy '{strategy_name}'. "
                f"Registered: {list(self._strategies.keys())}"
            )

        ctx = OrchestrationContext(
            coordinator=self,
            budget_usd=budget_usd or self.default_budget_usd,
            timeout=timeout or self.default_timeout,
        )

        strategy_fn = self._strategies[strategy_name]
        return await strategy_fn(task_description, ctx, **kwargs)

    def execute_sync(
        self,
        strategy_name: str,
        task_description: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Synchronous wrapper around execute() for non-async contexts."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    asyncio.run,
                    self.execute(strategy_name, task_description, **kwargs),
                )
                return future.result(timeout=self.default_timeout * 2)
        else:
            return asyncio.run(
                self.execute(strategy_name, task_description, **kwargs)
            )


@dataclass
class OrchestrationContext:
    """Passed to strategy functions. Provides fan_out, call_agent, synthesize."""

    coordinator: Coordinator
    budget_usd: float = 1.0
    timeout: float = 60.0
    session: AgentSession | None = field(default=None)
    task_tracker: TaskTracker = field(default_factory=TaskTracker)
    _spent_usd: float = field(default=0.0, init=False)

    def __post_init__(self):
        if self.session is None:
            self.session = AgentSession(conversation_id=generate_id())

    @property
    def budget_remaining(self) -> float:
        return max(0.0, self.budget_usd - self._spent_usd)

    async def fan_out(
        self,
        assignments: list[tuple[str, str]],
        timeout: float | None = None,
    ) -> list[FanOutResult]:
        """Dispatch multiple tasks in parallel and collect results."""
        results = await fan_out(
            agent=self.coordinator.agent,
            assignments=assignments,
            session=self.session,
            timeout=timeout or self.timeout,
            max_budget_usd=self.budget_remaining,
            max_concurrent=self.coordinator.max_concurrent,
            task_tracker=self.task_tracker,
        )

        for r in results:
            if r.usage:
                self._spent_usd += r.usage.get("cost_usd", 0.0)

        return results

    async def call_agent(
        self,
        capability: str,
        description: str,
        timeout: float | None = None,
    ) -> FanOutResult:
        """Call a single agent. Shorthand for fan_out with one assignment."""
        results = await self.fan_out(
            [(capability, description)],
            timeout=timeout,
        )
        return results[0]

    async def call_specific(
        self,
        webhook_url: str,
        message: InvokeMessage,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Call a specific agent by webhook URL directly."""
        keypair = getattr(self.coordinator.agent, "keypair", None)
        if keypair and not message.signature:
            from zyndai_agent.signatures import sign_message as _sign
            message.signature = _sign(message, keypair.private_key)

        x402_processor = getattr(self.coordinator.agent, "x402_processor", None)
        if x402_processor is None:
            raise RuntimeError("Agent has no x402_processor configured for outbound calls")

        try:
            resp = await asyncio.to_thread(
                x402_processor.session.post,
                webhook_url,
                json=message.model_dump(mode="json"),
                timeout=timeout or self.timeout,
            )
        except Exception as e:
            raise RuntimeError(f"Failed to call agent at {webhook_url}: {e}") from e

        try:
            return resp.json()
        except ValueError as e:
            raise RuntimeError(
                f"Agent at {webhook_url} returned non-JSON response (HTTP {resp.status_code})"
            ) from e

    def synthesize(self, results: list[FanOutResult]) -> dict[str, Any]:
        """Combine multiple fan-out results into a single structured response.

        Returns a dict with both machine-readable fields (``results``,
        ``failures``, ``agents_used``) and a human-readable ``briefing``
        string that downstream agents can consume directly instead of
        needing to parse raw JSON.
        """
        successful = [r for r in results if r.status == "success"]
        failed = [r for r in results if r.status != "success"]

        # Build a human-readable briefing from successful results
        briefing_parts: list[str] = []
        for r in successful:
            section_header = f"[{r.capability}]" if r.capability else "[result]"
            if r.agent_name:
                section_header += f" (from {r.agent_name})"

            if r.result:
                body = _format_result_for_briefing(r.result)
            else:
                body = "(no result data)"

            briefing_parts.append(f"{section_header}\n{body}")

        if failed:
            fail_lines = []
            for r in failed:
                fail_lines.append(f"  - {r.capability}: {r.error or 'unknown error'}")
            briefing_parts.append("[failures]\n" + "\n".join(fail_lines))

        briefing = "\n\n".join(briefing_parts)

        return {
            "status": "success" if successful else "error",
            "briefing": briefing,
            "results": [r.result for r in successful],
            "failures": [
                {"agent": r.agent_name, "capability": r.capability, "error": r.error}
                for r in failed
            ],
            "total_cost_usd": self._spent_usd,
            "agents_used": [r.agent_name for r in successful],
        }


def _format_result_for_briefing(result: dict[str, Any]) -> str:
    """Convert a result dict into readable text for downstream agents.

    Handles common patterns: lists of findings/trends, key-value metrics,
    and nested dicts.  Falls back to a compact JSON representation only
    when the structure is unrecognisable.
    """
    lines: list[str] = []

    for key, value in result.items():
        if isinstance(value, list):
            lines.append(f"  {key}:")
            for item in value:
                lines.append(f"    - {item}")
        elif isinstance(value, dict):
            lines.append(f"  {key}:")
            for k, v in value.items():
                lines.append(f"    {k}: {v}")
        elif isinstance(value, float):
            lines.append(f"  {key}: {value:.2f}")
        else:
            lines.append(f"  {key}: {value}")

    return "\n".join(lines) if lines else str(result)
