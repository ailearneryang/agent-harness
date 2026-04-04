"""Agent 基类测试"""
import asyncio
import pytest
from agent_harness.agent import Agent, AgentContext, AgentResult, AgentState


class OkAgent(Agent):
    async def run(self, ctx: AgentContext) -> AgentResult:
        return AgentResult(success=True, data={"msg": "ok"})


class FailAgent(Agent):
    async def run(self, ctx: AgentContext) -> AgentResult:
        return AgentResult(success=False, error="intentional failure")


class SlowAgent(Agent):
    async def run(self, ctx: AgentContext) -> AgentResult:
        await asyncio.sleep(10)
        return AgentResult(success=True)


class ErrorAgent(Agent):
    async def run(self, ctx: AgentContext) -> AgentResult:
        raise RuntimeError("unexpected error")


async def test_agent_success():
    agent = OkAgent(name="ok")
    ctx = AgentContext()
    result = await agent.execute(ctx)
    assert result.success is True
    assert result.data == {"msg": "ok"}
    assert agent.state == AgentState.SUCCESS


async def test_agent_failure():
    agent = FailAgent(name="fail")
    ctx = AgentContext()
    result = await agent.execute(ctx)
    assert result.success is False
    assert result.error == "intentional failure"
    assert agent.state == AgentState.FAILED


async def test_agent_timeout():
    agent = SlowAgent(name="slow", timeout=0.1)
    ctx = AgentContext()
    result = await agent.execute(ctx)
    assert result.success is False
    assert "超时" in result.error
    assert agent.state == AgentState.FAILED


async def test_agent_exception():
    agent = ErrorAgent(name="err")
    ctx = AgentContext()
    result = await agent.execute(ctx)
    assert result.success is False
    assert "unexpected error" in result.error


async def test_agent_health_check():
    agent = OkAgent(name="ok")
    assert await agent.health_check() is True


def test_agent_context_set_get():
    ctx = AgentContext()
    ctx.set("key", "value")
    assert ctx.get("key") == "value"
    assert ctx.get("missing", "default") == "default"


def test_agent_context_workspace(tmp_path):
    ctx = AgentContext(workspace=tmp_path)
    ws = ctx.ensure_workspace()
    assert ws.exists()
    assert ws == tmp_path
