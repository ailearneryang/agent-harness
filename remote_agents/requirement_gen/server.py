"""需求生成 Agent - 独立 FastAPI 服务

根据用户 prompt 生成结构化的软件需求文档。
如果收到评审意见（last_error），根据意见修改需求。
"""
from __future__ import annotations

import os
import time

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Requirement Generator Agent")

# LLM 配置
API_KEY = os.environ.get("OPENAI_API_KEY", "")
BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")


class RunRequest(BaseModel):
    prompt: str = ""
    workspace: str = ""
    shared: dict = {}
    last_error: str | None = None
    loop_count: int = 0


class RunResponse(BaseModel):
    success: bool
    data: dict | None = None
    error: str | None = None
    tokens_used: int = 0
    estimated_cost: float = 0.0


@app.get("/health")
async def health():
    return {"status": "ok", "agent": "requirement_gen", "timestamp": time.time()}


@app.post("/run")
async def run(req: RunRequest) -> RunResponse:
    prompt = req.prompt
    last_error = req.last_error
    loop_count = req.loop_count

    if API_KEY:
        return await _run_llm(prompt, last_error, loop_count, req.workspace)
    return _run_mock(prompt, last_error, loop_count, req.workspace)


async def _run_llm(prompt: str, last_error: str | None, loop_count: int, workspace: str) -> RunResponse:
    import httpx

    if last_error and loop_count > 0:
        system_msg = (
            "你是一个需求分析专家。根据评审意见修改需求文档。\n"
            "输出格式：\n"
            "# 需求文档\n## 功能需求\n## 非功能需求\n## 验收标准\n"
            "只返回修改后的完整需求文档，不要解释。"
        )
        user_msg = f"原始需求：{prompt}\n\n评审意见：\n{last_error}\n\n请根据评审意见修改需求文档。"
    else:
        system_msg = (
            "你是一个需求分析专家。根据用户描述生成结构化的软件需求文档。\n"
            "输出格式：\n"
            "# 需求文档\n## 功能需求\n## 非功能需求\n## 验收标准\n"
            "只返回需求文档，不要解释。"
        )
        user_msg = f"请为以下需求生成详细的需求文档：\n\n{prompt}"

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {API_KEY}"},
                json={
                    "model": MODEL,
                    "messages": [
                        {"role": "system", "content": system_msg},
                        {"role": "user", "content": user_msg},
                    ],
                    "temperature": 0.3,
                },
            )
            resp.raise_for_status()
            resp_data = resp.json()
            content = resp_data["choices"][0]["message"]["content"]
            # 兼容不同 LLM API 的 usage 格式
            usage = resp_data.get("usage", {})
            tokens = (
                usage.get("total_tokens", 0)
                or usage.get("totalTokens", 0)
                or (usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0))
            )
            print(f"[RequirementGen] tokens={tokens}, usage={usage}")
    except Exception as e:
        return RunResponse(success=False, error=f"LLM 调用失败: {e}")

    _write_to_workspace(workspace, "requirement.md", content)

    return RunResponse(
        success=True,
        tokens_used=tokens,
        estimated_cost=round(tokens * 0.0001 / 1000, 6),
        data={
            "file": "requirement.md",
            "action": "fix" if last_error else "create",
            "loop_count": loop_count,
            "preview": content[:200],
        },
    )


def _run_mock(prompt: str, last_error: str | None, loop_count: int, workspace: str) -> RunResponse:
    """Mock 模式：没有 API key 时使用"""
    if last_error and loop_count > 0:
        content = (
            f"# 需求文档（修订版 v{loop_count + 1}）\n\n"
            f"## 原始需求\n{prompt}\n\n"
            f"## 根据评审意见修改\n评审意见：{last_error}\n\n"
            "## 功能需求\n"
            "1. 用户登录/注册功能\n"
            "2. 数据 CRUD 操作\n"
            "3. 权限控制（已补充）\n"
            "4. 操作日志记录（已补充）\n\n"
            "## 非功能需求\n"
            "1. 响应时间 < 200ms\n"
            "2. 支持 1000 并发\n"
            "3. 数据加密存储（已补充）\n\n"
            "## 验收标准\n"
            "1. 所有功能需求通过测试\n"
            "2. 性能指标达标\n"
            "3. 安全审计通过（已补充）\n"
        )
    else:
        content = (
            f"# 需求文档\n\n"
            f"## 原始需求\n{prompt}\n\n"
            "## 功能需求\n"
            "1. 用户登录/注册功能\n"
            "2. 数据 CRUD 操作\n\n"
            "## 非功能需求\n"
            "1. 响应时间 < 500ms\n"
            "2. 支持 100 并发\n\n"
            "## 验收标准\n"
            "1. 所有功能需求通过测试\n"
            "2. 性能指标达标\n"
        )

    _write_to_workspace(workspace, "requirement.md", content)

    # 模拟 token 消耗
    mock_tokens = len(content) * 2  # 粗略估算：字符数 * 2

    return RunResponse(
        success=True,
        tokens_used=mock_tokens,
        estimated_cost=round(mock_tokens * 0.0001 / 1000, 6),
        data={
            "file": "requirement.md",
            "action": "fix" if last_error else "create",
            "loop_count": loop_count,
            "preview": content[:200],
        },
    )


def _write_to_workspace(workspace: str, filename: str, content: str):
    """写文件到 workspace"""
    if workspace:
        from pathlib import Path
        ws = Path(workspace)
        ws.mkdir(parents=True, exist_ok=True)
        (ws / filename).write_text(content)
