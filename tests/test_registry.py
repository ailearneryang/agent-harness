"""Registry 测试"""
import pytest
from agent_harness.agent import Agent, AgentContext, AgentResult
from agent_harness.registry import AgentRegistry


class DummyAgent(Agent):
    async def run(self, ctx: AgentContext) -> AgentResult:
        return AgentResult(success=True)


def test_register_and_get():
    reg = AgentRegistry()
    agent = DummyAgent(name="A")
    reg.register(agent, description="test", category="general")
    assert reg.get("A") is agent


def test_unregister():
    reg = AgentRegistry()
    agent = DummyAgent(name="A")
    reg.register(agent)
    assert reg.unregister("A") is True
    assert reg.get("A") is None


def test_enable_disable():
    reg = AgentRegistry()
    agent = DummyAgent(name="A")
    reg.register(agent)
    reg.disable("A")
    assert reg.get_meta("A").enabled is False
    reg.enable("A")
    assert reg.get_meta("A").enabled is True


def test_list_by_category():
    reg = AgentRegistry()
    reg.register(DummyAgent(name="A"), category="codegen")
    reg.register(DummyAgent(name="B"), category="test")
    reg.register(DummyAgent(name="C"), category="codegen")
    codegen = reg.list_by_category("codegen")
    assert len(codegen) == 2
    assert all(m.category == "codegen" for m in codegen)


def test_build_pipeline_agents_deepcopy():
    """每次 build 应该返回新实例，不是同一个对象"""
    reg = AgentRegistry()
    agent = DummyAgent(name="A")
    reg.register(agent)
    agents1 = reg.build_pipeline_agents(["A"])
    agents2 = reg.build_pipeline_agents(["A"])
    assert agents1[0] is not agents2[0]


def test_build_pipeline_agents_skips_disabled():
    reg = AgentRegistry()
    reg.register(DummyAgent(name="A"))
    reg.register(DummyAgent(name="B"))
    reg.disable("B")
    agents = reg.build_pipeline_agents(["A", "B"])
    assert len(agents) == 1
    assert agents[0].name == "A"


def test_update_config():
    reg = AgentRegistry()
    reg.register(DummyAgent(name="A"))
    reg.update_config("A", {"model": "gpt-4"})
    assert reg.get_meta("A").config["model"] == "gpt-4"
