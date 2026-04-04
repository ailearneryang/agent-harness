"""部署 Agent"""
from __future__ import annotations

import asyncio

from agent_harness.agent import Agent, AgentContext, AgentResult


class DeployerAgent(Agent):
    """部署 agent"""

    def __init__(self, name: str = "Deployer", timeout: float = 300.0):
        super().__init__(name=name, timeout=timeout)

    async def run(self, ctx: AgentContext) -> AgentResult:
        await asyncio.sleep(0.6)
        return AgentResult(
            success=True,
            data={"env": "staging", "version": "1.0.1", "status": "deployed"},
        )
