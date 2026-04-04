"""Pipeline 和 Harness 核心流程测试"""
import pytest
from agent_harness.agent import Agent, AgentContext, AgentResult
from agent_harness.harness import Harness
from agent_harness.monitor import Monitor, Event
from agent_harness.pipeline import Pipeline
from agent_harness.retry import FixedRetry


# --- 测试用 Monitor（不写 DB）---
class MemoryMonitor(Monitor):
    def __init__(self):
        self.events = []

    def on_event(self, event: Event) -> None:
        self.events.append(event)

    def event_types(self):
        return [e.event_type for e in self.events]


# --- 测试用 Agents ---
class OkAgent(Agent):
    async def run(self, ctx: AgentContext) -> AgentResult:
        ctx.set(f"{self.name}_ran", True)
        return AgentResult(success=True, data={"agent": self.name})


class FailOnceAgent(Agent):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._count = 0

    async def run(self, ctx: AgentContext) -> AgentResult:
        self._count += 1
        if self._count == 1:
            return AgentResult(success=False, error="first attempt fails")
        return AgentResult(success=True, data={"attempt": self._count})


class AlwaysFailAgent(Agent):
    async def run(self, ctx: AgentContext) -> AgentResult:
        return AgentResult(success=False, error="always fails")


async def make_harness():
    mon = MemoryMonitor()
    h = Harness(monitor=mon)
    return h, mon


async def test_linear_pipeline(tmp_path):
    h, mon = await make_harness()
    pipeline = (
        Pipeline("test")
        .add(OkAgent(name="A"))
        .add(OkAgent(name="B"))
        .add(OkAgent(name="C"))
    )
    ctx = AgentContext(workspace=tmp_path)
    result = await h.run(pipeline, ctx)
    assert result.success is True
    assert len(result.step_results) == 3
    assert "pipeline_complete" in mon.event_types()


async def test_step_retry(tmp_path):
    h, mon = await make_harness()
    pipeline = Pipeline("test").add(
        FailOnceAgent(name="flaky"),
        retry=FixedRetry(max_retries=2, delay=0),
    )
    ctx = AgentContext(workspace=tmp_path)
    result = await h.run(pipeline, ctx)
    assert result.success is True
    assert "step_retry" in mon.event_types()


async def test_abort_on_fail(tmp_path):
    h, mon = await make_harness()
    pipeline = (
        Pipeline("test")
        .add(AlwaysFailAgent(name="fail"), abort_on_fail=True)
        .add(OkAgent(name="should_not_run"))
    )
    ctx = AgentContext(workspace=tmp_path)
    result = await h.run(pipeline, ctx)
    assert result.success is False
    assert len(result.step_results) == 1  # 第二步没跑
    assert "pipeline_aborted" in mon.event_types()


async def test_loop_back(tmp_path):
    """测试失败回退：fail → 回到 A → 再次 fail → 回到 A → 成功"""
    h, mon = await make_harness()
    pipeline = (
        Pipeline("test")
        .add(OkAgent(name="A"))
        .add(FailOnceAgent(name="B"), on_fail_goto="A", max_loops=3)
    )
    ctx = AgentContext(workspace=tmp_path)
    result = await h.run(pipeline, ctx)
    assert result.success is True
    assert "loop_back" in mon.event_types()


async def test_condition_skip(tmp_path):
    h, mon = await make_harness()
    pipeline = (
        Pipeline("test")
        .add(AlwaysFailAgent(name="A"), abort_on_fail=False)
        .add(
            OkAgent(name="B"),
            condition=lambda ctx, prev: prev is not None and prev.get("success", False),
        )
    )
    ctx = AgentContext(workspace=tmp_path)
    result = await h.run(pipeline, ctx)
    assert "step_skipped" in mon.event_types()


async def test_parallel_execution(tmp_path):
    h, mon = await make_harness()
    pipeline = Pipeline("test").add_parallel([
        OkAgent(name="P1"),
        OkAgent(name="P2"),
        OkAgent(name="P3"),
    ])
    ctx = AgentContext(workspace=tmp_path)
    result = await h.run(pipeline, ctx)
    assert result.success is True
    assert "parallel_start" in mon.event_types()
    assert "parallel_end" in mon.event_types()


async def test_cancel(tmp_path):
    """取消标记在 step 间隙生效"""
    h, mon = await make_harness()

    pipeline = Pipeline("test").add(OkAgent(name="A")).add(OkAgent(name="B"))
    ctx = AgentContext(workspace=tmp_path)

    # 在 run 开始后立即注入取消
    original_execute = h._execute_step

    async def patched_execute(step, ctx, index, pipeline_id):
        result = await original_execute(step, ctx, index, pipeline_id)
        # 第一步完成后触发取消
        if index == 0:
            h.cancel(pipeline_id)
        return result

    h._execute_step = patched_execute
    result = await h.run(pipeline, ctx)

    assert result.success is False
    assert "取消" in result.error
    assert len(result.step_results) == 1  # 只跑了第一步
