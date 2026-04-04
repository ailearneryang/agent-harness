"""示例：开发 → 测试闭环

模拟场景：
1. CodeAgent 生成代码
2. TestAgent 运行测试
3. 如果测试失败，harness 自动重试（TestAgent 会在第2次通过）
"""
import asyncio
import logging
import random

from agent_harness import (
    Agent, AgentContext, AgentResult,
    Harness, Pipeline,
    ExponentialBackoff, ConsoleMonitor,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")


class CodeAgent(Agent):
    """模拟代码生成 agent"""

    async def run(self, ctx: AgentContext) -> AgentResult:
        code = "def add(a, b): return a + b"
        ctx.set("generated_code", code)
        return AgentResult(success=True, data={"code": code})


class TestAgent(Agent):
    """模拟测试 agent，第一次大概率失败，重试后通过"""

    def __init__(self):
        super().__init__(name="TestAgent")
        self._attempt = 0

    async def run(self, ctx: AgentContext) -> AgentResult:
        code = ctx.get("generated_code", "")
        self._attempt += 1

        # 模拟：第一次失败，第二次成功
        if self._attempt < 2:
            return AgentResult(
                success=False,
                error="AssertionError: test_add failed",
                data={"code_under_test": code},
            )
        return AgentResult(success=True, data={"tests_passed": 3, "tests_failed": 0})


class ReviewAgent(Agent):
    """模拟代码审查 agent"""

    async def run(self, ctx: AgentContext) -> AgentResult:
        return AgentResult(success=True, data={"review": "LGTM"})


async def main():
    # 构建 pipeline
    pipeline = (
        Pipeline(name="dev-test-loop")
        .add(CodeAgent(name="CodeGen"))
        .add(
            TestAgent(),
            retry=ExponentialBackoff(max_retries=3, base_delay=0.5),
        )
        .add(
            ReviewAgent(name="CodeReview"),
            # 只有测试通过才执行 review
            condition=lambda ctx, prev: prev is not None and prev.get("success", False),
        )
    )

    # 运行
    monitor = ConsoleMonitor()
    harness = Harness(monitor=monitor)
    result = await harness.run(pipeline)

    print(f"\n{'='*50}")
    print(f"Pipeline: {result.pipeline_id[:8]}")
    print(f"Success: {result.success}")
    for sr in result.step_results:
        r = sr["result"]
        status = "✅" if r.success else "❌"
        print(f"  Step {sr['step']} [{sr['agent']}]: {status} ({r.duration:.2f}s, data={r.data})")
    if result.error:
        print(f"Error: {result.error}")


if __name__ == "__main__":
    asyncio.run(main())
