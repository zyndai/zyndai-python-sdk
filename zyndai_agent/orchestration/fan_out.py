"""
Parallel agent dispatch and result collection.

fan_out() discovers agents by capability via the registry, sends typed
InvokeMessages in parallel, and collects results — all with automatic
x402 payment handling via the agent's existing requests.Session.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, TYPE_CHECKING

import requests as requests_lib

from zyndai_agent.typed_messages import (
    InvokeMessage,
    InvokeResponse,
    generate_id,
    parse_message,
)
from zyndai_agent.signatures import sign_message
from zyndai_agent.orchestration.task import Task, TaskTracker

if TYPE_CHECKING:
    from zyndai_agent.session import AgentSession

logger = logging.getLogger(__name__)


@dataclass
class FanOutResult:
    capability: str
    agent_name: str = ""
    agent_url: str = ""
    status: Literal["success", "error", "timeout"] = "error"
    result: dict[str, Any] | None = None
    error: str | None = None
    usage: dict[str, Any] | None = None
    task: Task | None = None


async def fan_out(
    agent: Any,
    assignments: list[tuple[str, str]],
    session: AgentSession | None = None,
    timeout: float = 60.0,
    max_budget_usd: float = 1.0,
    max_concurrent: int = 10,
    task_tracker: TaskTracker | None = None,
) -> list[FanOutResult]:
    """
    Dispatch multiple tasks to agents in parallel and collect results.

    Args:
        agent: ZyndAIAgent instance (used for search, signing, and x402 session).
        assignments: List of (capability_keyword, task_description) tuples.
        session: Optional AgentSession for conversation tracking.
        timeout: Per-task timeout in seconds.
        max_budget_usd: Total budget across all assignments.
        max_concurrent: Max parallel HTTP calls.
        task_tracker: Optional TaskTracker for lifecycle tracking.

    Returns:
        List of FanOutResult in same order as input assignments.
    """
    if timeout <= 0:
        raise ValueError(f"timeout must be positive, got {timeout}")

    if task_tracker is None:
        task_tracker = TaskTracker()

    semaphore = asyncio.Semaphore(max_concurrent)
    per_task_budget = max_budget_usd / max(len(assignments), 1)
    conversation_id = session.conversation_id if session else generate_id()

    async def _run_one(capability: str, description: str) -> FanOutResult:
        async with semaphore:
            task = task_tracker.create_task(
                description=description,
                timeout_seconds=timeout,
                max_budget_usd=per_task_budget,
            )

            try:
                agents_found = await asyncio.to_thread(
                    agent.search_entities, keyword=capability, limit=3
                )
            except Exception as e:
                task.mark_failed(f"Registry search failed: {e}")
                return FanOutResult(
                    capability=capability,
                    status="error",
                    error=f"Registry search failed: {e}",
                    task=task,
                )

            if not agents_found:
                task.mark_failed(f"No agent or service found for '{capability}'")
                return FanOutResult(
                    capability=capability,
                    status="error",
                    error=f"No agent or service found for '{capability}'",
                    task=task,
                )

            target = agents_found[0]
            target_name = target.get("name", "unknown")
            entity_type = target.get("entity_type") or target.get("type", "agent")

            # Services get a direct HTTP call, agents get InvokeMessage
            if entity_type == "service":
                endpoint = target.get("service_endpoint") or target.get("entity_url") or target.get("agent_url", "")
                task.assigned_to = endpoint
                task.mark_running()

                x402_proc = getattr(agent, "x402_processor", None)
                http_session = x402_proc.session if x402_proc and hasattr(x402_proc, "session") else requests_lib

                try:
                    # Services have varied APIs — try multiple patterns:
                    # 1. GET /search?query=... (search-style services)
                    # 2. GET /?query=... (generic query)
                    # 3. POST / with JSON body (action-style services)
                    resp = None
                    for attempt_url, attempt_params, attempt_method in [
                        (f"{endpoint}/search", {"query": description}, "GET"),
                        (endpoint, {"query": description}, "GET"),
                        (endpoint, None, "POST"),
                    ]:
                        try:
                            if attempt_method == "GET":
                                resp = await asyncio.to_thread(
                                    http_session.get, attempt_url,
                                    params=attempt_params, timeout=timeout,
                                )
                            else:
                                resp = await asyncio.to_thread(
                                    http_session.post, attempt_url,
                                    json={"query": description, "task": description},
                                    timeout=timeout,
                                )
                            if resp.status_code < 400:
                                break
                        except Exception:
                            continue

                    if resp is None:
                        raise RuntimeError("All request patterns failed")
                    if resp.status_code < 400:
                        try:
                            result_dict = resp.json()
                        except Exception:
                            result_dict = {"raw": resp.text}
                        task.mark_completed(result_dict)
                        return FanOutResult(
                            capability=capability, agent_name=target_name,
                            agent_url=endpoint, status="success",
                            result=result_dict, task=task,
                        )
                    else:
                        error = f"Service returned HTTP {resp.status_code}"
                        task.mark_failed(error)
                        return FanOutResult(
                            capability=capability, agent_name=target_name,
                            agent_url=endpoint, status="error", error=error, task=task,
                        )
                except Exception as e:
                    task.mark_failed(str(e))
                    return FanOutResult(
                        capability=capability, agent_name=target_name,
                        agent_url=endpoint, status="error", error=str(e), task=task,
                    )

            # Agent path: InvokeMessage to webhook
            agent_url = target.get("entity_url") or target.get("agent_url", "")
            invoke_url = f"{agent_url.rstrip('/')}/webhook/sync"

            task.assigned_to = invoke_url
            task.mark_running()

            msg = InvokeMessage(
                conversation_id=conversation_id,
                sender_id=getattr(agent, "entity_id", "unknown"),
                sender_public_key=getattr(agent.keypair, "public_key_string", None) if getattr(agent, "keypair", None) else None,
                capability=capability,
                payload={"task": description},
                max_budget_usd=per_task_budget,
                timeout_seconds=int(timeout),
            )

            keypair = getattr(agent, "keypair", None)
            if keypair:
                msg.signature = sign_message(msg, keypair.private_key)

            x402_processor = getattr(agent, "x402_processor", None)
            if x402_processor is None:
                task.mark_failed("Agent has no x402_processor for outbound calls")
                return FanOutResult(
                    capability=capability,
                    agent_name=target_name,
                    agent_url=agent_url,
                    status="error",
                    error="Agent has no x402_processor for outbound calls",
                    task=task,
                )

            try:
                resp = await asyncio.to_thread(
                    x402_processor.session.post,
                    invoke_url,
                    json=msg.model_dump(mode="json"),
                    timeout=timeout,
                )

                resp_data = resp.json()

                if resp.status_code == 200 and resp_data.get("status") == "success":
                    response_content = resp_data.get("response", resp_data)
                    if isinstance(response_content, dict):
                        result_dict = dict(response_content)
                        usage = result_dict.pop("usage", None)
                    else:
                        result_dict = {"content": response_content}
                        usage = None
                    task.mark_completed(result_dict, usage)

                    if session:
                        try:
                            typed_resp = parse_message(resp_data) if "type" in resp_data else None
                            if typed_resp:
                                session.add_message(typed_resp)
                        except Exception as e:
                            logger.warning(f"Failed to record response in session for capability={capability}: {e}")

                    return FanOutResult(
                        capability=capability,
                        agent_name=target_name,
                        agent_url=agent_url,
                        status="success",
                        result=result_dict,
                        usage=usage,
                        task=task,
                    )
                else:
                    error = resp_data.get("error", f"HTTP {resp.status_code}")
                    task.mark_failed(error)
                    return FanOutResult(
                        capability=capability,
                        agent_name=target_name,
                        agent_url=agent_url,
                        status="error",
                        error=error,
                        task=task,
                    )

            except (requests_lib.exceptions.Timeout, requests_lib.exceptions.ConnectTimeout, requests_lib.exceptions.ReadTimeout):
                task.mark_timed_out()
                return FanOutResult(
                    capability=capability,
                    agent_name=target_name,
                    agent_url=agent_url,
                    status="timeout",
                    error=f"Timed out after {timeout}s",
                    task=task,
                )
            except Exception as e:
                task.mark_failed(str(e))
                return FanOutResult(
                    capability=capability,
                    agent_name=target_name,
                    agent_url=agent_url,
                    status="error",
                    error=str(e),
                    task=task,
                )

    results = await asyncio.gather(
        *[_run_one(cap, desc) for cap, desc in assignments],
        return_exceptions=True,
    )

    final: list[FanOutResult] = []
    for i, r in enumerate(results):
        if isinstance(r, FanOutResult):
            final.append(r)
        else:
            cap = assignments[i][0] if i < len(assignments) else "unknown"
            logger.error(f"Unexpected exception in fan_out for capability='{cap}': {r!r}")
            final.append(FanOutResult(capability=cap, status="error", error=str(r)))
    return final
