"""Tests for orchestration: task state machine, fan_out, coordinator."""

import asyncio
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime, timezone

from zyndai_agent.orchestration.task import Task, TaskStatus, TaskTracker
from zyndai_agent.orchestration.fan_out import fan_out, FanOutResult
from zyndai_agent.orchestration.coordinator import Coordinator, OrchestrationContext
from zyndai_agent.typed_messages import InvokeMessage


# --- Task State Machine ---


class TestTaskStatus:
    def test_pending_by_default(self):
        t = Task(description="test")
        assert t.status == TaskStatus.PENDING
        assert not t.is_terminal

    def test_mark_running(self):
        t = Task(description="test")
        t.mark_running()
        assert t.status == TaskStatus.RUNNING
        assert t.started_at is not None
        assert not t.is_terminal

    def test_mark_completed(self):
        t = Task(description="test")
        t.mark_running()
        t.mark_completed({"answer": 42}, {"cost_usd": 0.01})
        assert t.status == TaskStatus.COMPLETED
        assert t.result == {"answer": 42}
        assert t.usage["cost_usd"] == 0.01
        assert t.is_terminal
        assert t.completed_at is not None

    def test_mark_failed(self):
        t = Task(description="test")
        t.mark_running()
        t.mark_failed("connection refused")
        assert t.status == TaskStatus.FAILED
        assert t.error == "connection refused"
        assert t.is_terminal

    def test_mark_cancelled(self):
        t = Task(description="test")
        t.mark_cancelled()
        assert t.status == TaskStatus.CANCELLED
        assert t.is_terminal

    def test_mark_timed_out(self):
        t = Task(description="test", timeout_seconds=10.0)
        t.mark_timed_out()
        assert t.status == TaskStatus.TIMED_OUT
        assert "10.0" in t.error

    def test_duration_ms(self):
        t = Task(description="test")
        assert t.duration_ms is None
        t.mark_running()
        import time
        time.sleep(0.01)
        t.mark_completed({})
        assert t.duration_ms > 0


class TestTaskTracker:
    def test_create_and_get(self):
        tracker = TaskTracker()
        t = tracker.create_task("search papers", assigned_to="agent-a")
        assert tracker.get_task(t.task_id) is t

    def test_active_tasks(self):
        tracker = TaskTracker()
        t1 = tracker.create_task("task 1")
        t2 = tracker.create_task("task 2")
        t1.mark_running()
        t2.mark_completed({})
        active = tracker.active_tasks()
        assert len(active) == 1
        assert active[0] is t1

    def test_completed_tasks(self):
        tracker = TaskTracker()
        t = tracker.create_task("task")
        t.mark_completed({"done": True})
        assert len(tracker.completed_tasks()) == 1

    def test_total_cost(self):
        tracker = TaskTracker()
        t1 = tracker.create_task("t1")
        t2 = tracker.create_task("t2")
        t1.mark_completed({}, {"cost_usd": 0.03})
        t2.mark_completed({}, {"cost_usd": 0.07})
        assert tracker.total_cost() == pytest.approx(0.10)

    def test_summary(self):
        tracker = TaskTracker()
        t1 = tracker.create_task("t1")
        t2 = tracker.create_task("t2")
        t1.mark_completed({})
        s = tracker.summary()
        assert s["total"] == 2
        assert s["by_status"]["completed"] == 1
        assert s["by_status"]["pending"] == 1

    def test_get_nonexistent(self):
        tracker = TaskTracker()
        assert tracker.get_task("bogus") is None


# --- Fan Out ---


class TestFanOut:
    @pytest.fixture
    def mock_agent(self):
        agent = MagicMock()
        agent.agent_id = "coordinator-1"
        agent.keypair = None
        agent.x402_processor.session = MagicMock()
        return agent

    @pytest.mark.asyncio
    async def test_no_agents_found(self, mock_agent):
        mock_agent.search_agents = MagicMock(return_value=[])

        results = await fan_out(
            agent=mock_agent,
            assignments=[("nonexistent-skill", "do something")],
        )
        assert len(results) == 1
        assert results[0].status == "error"
        assert "No agent found" in results[0].error

    @pytest.mark.asyncio
    async def test_successful_call(self, mock_agent):
        mock_agent.search_agents = MagicMock(return_value=[
            {"name": "translator", "agent_url": "http://localhost:5001"}
        ])

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "success",
            "response": {"translated": "bonjour"},
        }
        mock_agent.x402_processor.session.post = MagicMock(return_value=mock_response)

        results = await fan_out(
            agent=mock_agent,
            assignments=[("translate", "translate hello to French")],
        )
        assert len(results) == 1
        assert results[0].status == "success"
        assert results[0].agent_name == "translator"
        assert results[0].result["translated"] == "bonjour"

    @pytest.mark.asyncio
    async def test_partial_failure(self, mock_agent):
        call_count = {"n": 0}

        def fake_search(keyword=None, limit=3):
            call_count["n"] += 1
            return [{"name": f"agent-{keyword}", "agent_url": f"http://{keyword}.local"}]

        mock_agent.search_agents = fake_search

        def fake_post(url, json=None, timeout=None):
            resp = MagicMock()
            if "translate.local" in url:
                resp.status_code = 200
                resp.json.return_value = {"status": "success", "response": {"ok": True}}
            else:
                resp.status_code = 500
                resp.json.return_value = {"status": "error", "error": "internal error"}
            return resp

        mock_agent.x402_processor.session.post = fake_post

        results = await fan_out(
            agent=mock_agent,
            assignments=[
                ("translate", "translate hello"),
                ("failing-skill", "this will fail"),
            ],
        )
        assert len(results) == 2
        statuses = {r.capability: r.status for r in results}
        assert statuses["translate"] == "success"
        assert statuses["failing-skill"] == "error"

    @pytest.mark.asyncio
    async def test_http_exception(self, mock_agent):
        mock_agent.search_agents = MagicMock(return_value=[
            {"name": "broken", "agent_url": "http://localhost:9999"}
        ])
        mock_agent.x402_processor.session.post = MagicMock(
            side_effect=ConnectionError("refused")
        )

        results = await fan_out(
            agent=mock_agent,
            assignments=[("broken-skill", "call broken agent")],
        )
        assert results[0].status == "error"
        assert "refused" in results[0].error


# --- Coordinator ---


class TestCoordinator:
    @pytest.fixture
    def mock_agent(self):
        agent = MagicMock()
        agent.agent_id = "coord-1"
        agent.keypair = None
        agent.x402_processor.session = MagicMock()
        agent.search_agents = MagicMock(return_value=[])
        return agent

    def test_register_strategy(self, mock_agent):
        coord = Coordinator(agent=mock_agent)

        @coord.strategy("test")
        async def test_strategy(desc, ctx):
            return {"done": True}

        assert "test" in coord._strategies

    @pytest.mark.asyncio
    async def test_execute_strategy(self, mock_agent):
        coord = Coordinator(agent=mock_agent)

        @coord.strategy("echo")
        async def echo(desc, ctx):
            return {"echo": desc}

        result = await coord.execute("echo", "hello world")
        assert result == {"echo": "hello world"}

    @pytest.mark.asyncio
    async def test_unknown_strategy_raises(self, mock_agent):
        coord = Coordinator(agent=mock_agent)
        with pytest.raises(ValueError, match="Unknown strategy"):
            await coord.execute("nonexistent", "test")

    def test_execute_sync(self, mock_agent):
        coord = Coordinator(agent=mock_agent)

        @coord.strategy("sync-test")
        async def sync_test(desc, ctx):
            return {"sync": True}

        result = coord.execute_sync("sync-test", "test")
        assert result == {"sync": True}


class TestOrchestrationContext:
    def test_synthesize_all_success(self):
        ctx = OrchestrationContext(coordinator=MagicMock())
        results = [
            FanOutResult(capability="a", agent_name="agent-a", status="success", result={"x": 1}),
            FanOutResult(capability="b", agent_name="agent-b", status="success", result={"y": 2}),
        ]
        synth = ctx.synthesize(results)
        assert synth["status"] == "success"
        assert len(synth["results"]) == 2
        assert len(synth["failures"]) == 0

    def test_synthesize_partial_failure(self):
        ctx = OrchestrationContext(coordinator=MagicMock())
        results = [
            FanOutResult(capability="a", agent_name="agent-a", status="success", result={"x": 1}),
            FanOutResult(capability="b", agent_name="agent-b", status="error", error="timeout"),
        ]
        synth = ctx.synthesize(results)
        assert synth["status"] == "success"
        assert len(synth["results"]) == 1
        assert len(synth["failures"]) == 1
        assert synth["failures"][0]["error"] == "timeout"

    def test_synthesize_all_failures(self):
        ctx = OrchestrationContext(coordinator=MagicMock())
        results = [
            FanOutResult(capability="a", status="error", error="boom"),
        ]
        synth = ctx.synthesize(results)
        assert synth["status"] == "error"
        assert len(synth["results"]) == 0

    def test_budget_tracking(self):
        ctx = OrchestrationContext(coordinator=MagicMock(), budget_usd=1.0)
        assert ctx.budget_remaining == 1.0
        ctx._spent_usd = 0.3
        assert ctx.budget_remaining == pytest.approx(0.7)
