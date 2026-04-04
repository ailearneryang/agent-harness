"""代码审查 Agent - 从 workspace 读取代码进行审查"""
from __future__ import annotations

import asyncio

from agent_harness.agent import Agent, AgentContext, AgentResult


class ReviewerAgent(Agent):
    """代码审查 agent，从 workspace 读取代码文件进行审查"""

    def __init__(self, name: str = "Reviewer", timeout: float = 300.0):
        super().__init__(name=name, timeout=timeout)

    async def run(self, ctx: AgentContext) -> AgentResult:
        ws = ctx.ensure_workspace()
        target_file = ws / "solution.py"

        if not target_file.exists():
            return AgentResult(success=True, data={"review": "skipped", "reason": "no code file found"})

        code = target_file.read_text()
        await asyncio.sleep(0.5)

        # 模拟审查（实际可对接 LLM）
        issues = []
        if len(code.strip().split("\n")) < 3:
            issues.append("代码过于简短，建议增加文档字符串")
        if "# TODO" in code:
            issues.append("存在未完成的 TODO")

        return AgentResult(
            success=True,
            data={
                "review": "LGTM" if not issues else "needs_improvement",
                "score": 95 if not issues else 70,
                "suggestions": issues,
                "lines_reviewed": len(code.split("\n")),
            },
        )
