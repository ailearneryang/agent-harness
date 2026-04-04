"""Pipeline 定义 - 支持串行、并行、失败回退"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Any

from agent_harness.agent import Agent, AgentContext, AgentResult
from agent_harness.retry import RetryPolicy, FixedRetry


@dataclass
class PipelineStep:
    """Pipeline 中的一个步骤（串行）"""
    agent: Agent
    retry_policy: RetryPolicy = field(default_factory=lambda: FixedRetry(max_retries=0))
    condition: Callable[[AgentContext, AgentResult | None], bool] | None = None
    abort_on_fail: bool = True
    on_fail_goto: str | None = None
    max_loops: int = 3
    # 标记是否为并行组
    parallel: bool = False
    parallel_agents: list[Agent] = field(default_factory=list)


class Pipeline:
    """定义 agent 执行流程

    支持:
    - 线性执行: add(agent)
    - 并行执行: add_parallel([agent1, agent2]) — 同时执行多个 agent
    - 单步重试: add(agent, retry=...)
    - 失败回退: add(agent, on_fail_goto="CodeGen")
    """

    def __init__(self, name: str = "pipeline"):
        self.name = name
        self.steps: list[PipelineStep] = []

    def add(
        self,
        agent: Agent,
        retry: RetryPolicy | None = None,
        condition: Callable[[AgentContext, AgentResult | None], bool] | None = None,
        abort_on_fail: bool = True,
        on_fail_goto: str | None = None,
        max_loops: int = 3,
    ) -> "Pipeline":
        self.steps.append(
            PipelineStep(
                agent=agent,
                retry_policy=retry or FixedRetry(max_retries=0),
                condition=condition,
                abort_on_fail=abort_on_fail,
                on_fail_goto=on_fail_goto,
                max_loops=max_loops,
            )
        )
        return self

    def add_parallel(
        self,
        agents: list[Agent],
        abort_on_fail: bool = True,
    ) -> "Pipeline":
        """添加并行步骤组，所有 agent 同时执行"""
        names = "+".join(a.name for a in agents)
        # 用第一个 agent 作为占位，parallel_agents 存所有
        self.steps.append(
            PipelineStep(
                agent=agents[0],
                parallel=True,
                parallel_agents=agents,
                abort_on_fail=abort_on_fail,
            )
        )
        return self

    def get_step_index(self, agent_name: str) -> int | None:
        for i, step in enumerate(self.steps):
            if step.parallel:
                for a in step.parallel_agents:
                    if a.name == agent_name:
                        return i
            elif step.agent.name == agent_name:
                return i
        return None
