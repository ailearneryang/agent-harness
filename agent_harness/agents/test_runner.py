"""测试验证 Agent - 在 workspace 中执行 pytest（真实 subprocess）"""
from __future__ import annotations

import asyncio
import json

from agent_harness.agent import Agent, AgentContext, AgentResult


class TestRunnerAgent(Agent):
    """测试验证 agent

    工作方式：
    1. 在 workspace 目录下执行 pytest
    2. 解析测试结果，上报给 harness
    3. 支持 mock 模式（演示用）和 real 模式（真实跑 pytest）
    """

    def __init__(
        self,
        name: str = "TestRunner",
        mock: bool = True,
        fail_first_n: int = 1,
        timeout: float = 120.0,
    ):
        super().__init__(name=name, timeout=timeout)
        self.mock = mock
        self.fail_first_n = fail_first_n
        self._run_count = 0

    async def run(self, ctx: AgentContext) -> AgentResult:
        if self.mock:
            return await self._run_mock(ctx)
        return await self._run_real(ctx)

    async def _run_real(self, ctx: AgentContext) -> AgentResult:
        """真实执行 pytest"""
        ws = ctx.ensure_workspace()
        test_file = ws / "test_solution.py"

        if not test_file.exists():
            return AgentResult(success=False, error="workspace 中没有找到测试文件")

        proc = await asyncio.create_subprocess_exec(
            "python3", "-m", "pytest", str(test_file),
            "-v", "--tb=short", "--no-header",
            cwd=str(ws),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        output = stdout.decode()
        err_output = stderr.decode()

        # 解析结果
        passed = output.count(" PASSED")
        failed = output.count(" FAILED")
        errors = output.count(" ERROR")
        total = passed + failed + errors

        if proc.returncode == 0:
            return AgentResult(
                success=True,
                data={
                    "passed": passed, "failed": 0, "total": total,
                    "output": output[-500:],  # 截断，避免太长
                },
            )

        # 提取失败信息
        failure_lines = []
        for line in output.split("\n"):
            if "FAILED" in line or "AssertionError" in line or "Error" in line:
                failure_lines.append(line.strip())

        return AgentResult(
            success=False,
            error=f"pytest: {failed} failed, {passed} passed",
            data={
                "passed": passed, "failed": failed, "total": total,
                "failures": failure_lines[:10],
                "output": output[-500:],
            },
        )

    async def _run_mock(self, ctx: AgentContext) -> AgentResult:
        """Mock 模式，用于演示"""
        ws = ctx.ensure_workspace()
        test_file = ws / "test_solution.py"

        if not test_file.exists():
            return AgentResult(success=False, error="workspace 中没有找到测试文件")

        self._run_count += 1
        await asyncio.sleep(1.0)

        if self._run_count <= self.fail_first_n:
            return AgentResult(
                success=False,
                error=f"pytest: 2 failed, 1 passed (第 {self._run_count} 次执行)",
                data={"passed": 1, "failed": 2, "total": 3},
            )

        return AgentResult(
            success=True,
            data={"passed": 3, "failed": 0, "total": 3, "coverage": "87%"},
        )

    def reset(self) -> None:
        self._run_count = 0
