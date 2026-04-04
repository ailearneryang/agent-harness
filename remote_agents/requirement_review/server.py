"""需求评审 Agent - 独立 FastAPI 服务

读取 workspace 中的需求文档，进行评审。
评审通过返回 success=True，不通过返回 success=False + 评审意见。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Requirement Review Agent")

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


@app.get("/health")
async def health():
    return {"status": "ok", "agent": "requirement_review", "timestamp": time.time()}


@app.post("/run")
async def run(req: RunRequest) -> RunResponse:
    # 从 workspace 读取需求文档
    requirement = ""
    if req.workspace:
        req_file = Path(req.workspace) / "requirement.md"
        if req_file.exists():
            requirement = req_file.read_text()

    if not requirement:
        return RunResponse(success=False, error="workspace 中没有找到 requirement.md")

    loop_count = req.loop_count

    if API_KEY:
        return await _run_llm(requirement, loop_count, req.workspace)
    return _run_mock(requirement, loop_count, req.workspace)


async def _run_llm(requirement: str, loop_count: int, workspace: str) -> RunResponse:
    import httpx

    system_msg = (
        "你是一名新能源汽车领域的资深需求评审专家，熟悉以下标准和规范：\n"
        "- ISO 26262（功能安全）\n"
        "- ASPICE（汽车软件过程改进和能力评定）\n"
        "- GB/T 18386（电动汽车能量消耗量和续驶里程试验方法）\n"
        "- GB 18384（电动汽车安全要求）\n"
        "- UN R155/R156（网络安全与软件更新）\n\n"
        "请从以下维度评审需求文档：\n"
        "1. 功能安全：是否符合 ISO 26262 的 ASIL 等级要求，是否有安全目标和安全需求\n"
        "2. 网络安全：是否满足 UN R155 网络安全管理体系要求，是否有威胁分析和风险评估\n"
        "3. 三电系统（电池/电机/电控）：需求是否覆盖 BMS、MCU、VCU 等核心控制器\n"
        "4. OTA 升级：是否有软件版本管理、回滚机制、升级安全验证\n"
        "5. 合规性：是否满足国标 GB 18384 安全要求和 GB/T 18386 测试标准\n"
        "6. 验收标准：是否可测试、可量化，是否有 HIL/SIL 测试要求\n"
        "7. 可追溯性：需求是否可追溯到系统需求和测试用例\n\n"
        "如果需求文档质量合格（评分>=75），返回 JSON：\n"
        "{\"passed\": true, \"score\": 85, \"comments\": \"...\"}\n"
        "如果不合格，返回 JSON：\n"
        "{\"passed\": false, \"score\": 60, \"issues\": [\"问题1\", \"问题2\"]}\n"
        "只返回 JSON，不要其他内容。"
    )

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {API_KEY}"},
                json={
                    "model": MODEL,
                    "messages": [
                        {"role": "system", "content": system_msg},
                        {"role": "user", "content": f"请评审以下需求文档：\n\n{requirement}"},
                    ],
                    "temperature": 0.2,
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return RunResponse(success=False, error=f"LLM 调用失败: {e}")

    # 解析评审结果
    import json
    try:
        review = json.loads(content.strip().strip("`").replace("json\n", "").replace("json", ""))
    except json.JSONDecodeError:
        review = {"passed": False, "score": 0, "issues": ["无法解析评审结果"]}

    # 写评审报告到 workspace
    _write_review(workspace, review, loop_count)

    if review.get("passed"):
        return RunResponse(success=True, data={"review": review, "file": "review_report.md"})

    issues = review.get("issues", ["需求文档质量不达标"])
    return RunResponse(
        success=False,
        error="评审不通过: " + "; ".join(issues),
        data={"review": review, "file": "review_report.md"},
    )


def _run_mock(requirement: str, loop_count: int, workspace: str) -> RunResponse:
    """Mock 模式：按新能源汽车标准评审"""
    if loop_count < 1:
        issues = [
            "缺少 ISO 26262 功能安全等级（ASIL）定义，未明确安全目标",
            "未包含 UN R155 网络安全威胁分析和风险评估（TARA）",
            "BMS 电池管理系统需求缺失：未覆盖热管理、SOC/SOH 估算、均衡策略",
            "缺少 OTA 升级的回滚机制和版本管理策略",
            "验收标准未包含 HIL/SIL 测试要求",
            "需求可追溯性不足，未建立系统需求→软件需求→测试用例的追溯矩阵",
        ]
        review = {"passed": False, "score": 35, "issues": issues}
        _write_review(workspace, review, loop_count)
        return RunResponse(
            success=False,
            error="评审不通过: " + "; ".join(issues),
            data={"review": review, "file": "review_report.md"},
        )

    review = {
        "passed": True,
        "score": 82,
        "comments": (
            "需求文档已根据新能源汽车标准完善：\n"
            "- ISO 26262 功能安全等级已明确\n"
            "- 网络安全 TARA 分析已补充\n"
            "- 三电系统核心需求已覆盖\n"
            "- OTA 升级机制已完善\n"
            "- HIL/SIL 测试要求已纳入验收标准\n"
            "建议后续补充 ASPICE 过程域的详细映射。"
        ),
    }
    _write_review(workspace, review, loop_count)
    return RunResponse(success=True, data={"review": review, "file": "review_report.md"})


def _write_review(workspace: str, review: dict, loop_count: int):
    if workspace:
        ws = Path(workspace)
        ws.mkdir(parents=True, exist_ok=True)
        passed = "✅ 通过" if review.get("passed") else "❌ 不通过"
        content = (
            f"# 新能源汽车需求评审报告（第 {loop_count + 1} 轮）\n\n"
            f"## 评审结果：{passed}\n"
            f"## 评分：{review.get('score', 'N/A')}/100\n\n"
            "## 评审标准\n"
            "- ISO 26262 功能安全\n"
            "- UN R155/R156 网络安全与 OTA\n"
            "- GB 18384 电动汽车安全要求\n"
            "- ASPICE 软件过程能力\n\n"
        )
        if review.get("issues"):
            content += "## 发现的问题\n" + "\n".join(f"- {i}" for i in review["issues"]) + "\n"
        if review.get("comments"):
            content += f"\n## 评审意见\n{review['comments']}\n"
        (ws / "review_report.md").write_text(content)
