"""代码生成 Agent - 支持真实 LLM 和 Mock 模式"""
from __future__ import annotations

import asyncio
import os
from typing import Any

from agent_harness.agent import Agent, AgentContext, AgentResult


class CodeGenAgent(Agent):
    """代码生成 agent

    支持两种模式：
    - mock=True: 模拟生成（演示用）
    - mock=False: 调用真实 LLM API（OpenAI 兼容接口）

    配置项（通过 config 传入）：
    - api_key: LLM API key（也可通过环境变量 OPENAI_API_KEY）
    - base_url: API 地址（默认 OpenAI，可改为其他兼容接口）
    - model: 模型名称
    """

    def __init__(
        self,
        name: str = "CodeGen",
        mock: bool = True,
        config: dict[str, Any] | None = None,
        timeout: float = 120.0,
    ):
        super().__init__(name=name, timeout=timeout)
        self.mock = mock
        self.config = config or {}
        self._gen_count = 0

    async def run(self, ctx: AgentContext) -> AgentResult:
        ws = ctx.ensure_workspace()
        prompt = ctx.get("prompt", "实现一个加法函数")
        last_error = ctx.get("last_error")
        loop_count = ctx.get("loop_count", 0)
        target_file = ws / "solution.py"
        test_file = ws / "test_solution.py"

        self._gen_count += 1

        if self.mock:
            return await self._run_mock(
                ws, prompt, last_error, loop_count, target_file, test_file, ctx,
            )
        return await self._run_llm(
            ws, prompt, last_error, loop_count, target_file, test_file, ctx,
        )

    async def _run_llm(self, ws, prompt, last_error, loop_count, target_file, test_file, ctx):
        """调用真实 LLM API"""
        try:
            import httpx
        except ImportError:
            return AgentResult(success=False, error="需要安装 httpx: pip install httpx")

        api_key = self.config.get("api_key") or os.environ.get("OPENAI_API_KEY", "")
        base_url = self.config.get("base_url", "https://api.openai.com/v1")
        model = self.config.get("model", "gpt-4o-mini")

        if not api_key:
            return AgentResult(success=False, error="未配置 API key")

        # 构建 prompt
        if last_error and loop_count > 0:
            old_code = target_file.read_text() if target_file.exists() else ""
            system_msg = "你是一个代码修复专家。根据测试错误信息修改代码，只返回修改后的完整 Python 代码，不要解释。"
            user_msg = f"原始需求：{prompt}\n\n当前代码：\n```python\n{old_code}\n```\n\n测试错误：\n{last_error}\n\n请修复代码。"
        else:
            system_msg = "你是一个 Python 代码生成专家。根据需求生成代码，只返回 Python 代码，不要解释。"
            user_msg = f"需求：{prompt}\n\n请生成实现代码。"

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_msg},
                        {"role": "user", "content": user_msg},
                    ],
                    "temperature": 0.2,
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]

        # 提取代码块
        code = self._extract_code(content)
        target_file.write_text(code)

        # 首次生成时也生成测试
        if loop_count == 0 and not test_file.exists():
            test_code = await self._generate_tests_llm(api_key, base_url, model, prompt, code)
            test_file.write_text(test_code)

        ctx.set("last_error", None)
        action = "fix" if last_error and loop_count > 0 else "create"
        return AgentResult(
            success=True,
            data={"file": "solution.py", "action": action, "attempt": self._gen_count},
        )

    async def _generate_tests_llm(self, api_key, base_url, model, prompt, code):
        """用 LLM 生成测试用例"""
        import httpx
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "你是一个测试专家。为给定代码生成 pytest 测试用例，只返回 Python 代码，不要解释。"},
                        {"role": "user", "content": f"需求：{prompt}\n\n代码：\n```python\n{code}\n```\n\n请生成测试用例。"},
                    ],
                    "temperature": 0.2,
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return self._extract_code(content)

    def _extract_code(self, content: str) -> str:
        """从 LLM 响应中提取代码块"""
        if "```python" in content:
            parts = content.split("```python")
            if len(parts) > 1:
                code = parts[1].split("```")[0]
                return code.strip()
        if "```" in content:
            parts = content.split("```")
            if len(parts) > 1:
                return parts[1].strip()
        return content.strip()

    async def _run_mock(self, ws, prompt, last_error, loop_count, target_file, test_file, ctx):
        """Mock 模式"""
        await asyncio.sleep(0.8)

        if last_error and loop_count > 0:
            old_code = target_file.read_text() if target_file.exists() else ""
            new_code = (
                f'"""Fixed (attempt {self._gen_count}) based on: {last_error}"""\n\n\n'
                "def add(a: int, b: int) -> int:\n"
                '    """改进后的实现"""\n'
                "    return a + b\n"
            )
            target_file.write_text(new_code)
            ctx.set("last_error", None)
            return AgentResult(
                success=True,
                data={"file": "solution.py", "action": "fix", "reason": last_error, "attempt": self._gen_count},
            )

        code = (
            f'"""Generated from prompt: {prompt}"""\n\n\n'
            "def add(a: int, b: int) -> int:\n"
            "    return a + b\n"
        )
        target_file.write_text(code)
        test_file.write_text(
            "from solution import add\n\n\n"
            "def test_add_positive():\n"
            "    assert add(1, 2) == 3\n\n\n"
            "def test_add_negative():\n"
            "    assert add(-1, -2) == -3\n\n\n"
            "def test_add_zero():\n"
            "    assert add(0, 0) == 0\n"
        )
        return AgentResult(
            success=True,
            data={"file": "solution.py", "test_file": "test_solution.py", "action": "create", "attempt": self._gen_count},
        )

    def reset(self) -> None:
        self._gen_count = 0
