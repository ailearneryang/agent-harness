"""远程 Agent - 通过 HTTP 调用外部 agent 服务"""
from __future__ import annotations

import asyncio
from typing import Any

from agent_harness.agent import Agent, AgentContext, AgentResult


class RemoteAgent(Agent):
    """通过 HTTP API 调用远程 agent

    远程服务需要实现 POST 接口，接收 JSON：
    {
        "prompt": "...",
        "workspace": "...",
        "shared": {...},
        "last_error": "...",
        "loop_count": 0
    }

    返回 JSON：
    {
        "success": true/false,
        "data": {...},
        "error": "..."
    }
    """

    def __init__(
        self,
        name: str,
        endpoint: str,
        headers: dict[str, str] | None = None,
        timeout: float = 120.0,
        health_endpoint: str | None = None,
    ):
        super().__init__(name=name, timeout=timeout)
        self.endpoint = endpoint
        self.headers = headers or {}
        self.health_endpoint = health_endpoint

    async def health_check(self) -> bool:
        """探活：检查远程 agent 是否可用"""
        url = self.health_endpoint or self.endpoint.rsplit("/", 1)[0] + "/health"
        try:
            import httpx
            # transport 限制只用 IPv4，避免 IPv6 连接问题
            transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0")
            async with httpx.AsyncClient(timeout=5.0, transport=transport) as client:
                resp = await client.get(url, headers=self.headers)
                return resp.status_code == 200
        except Exception:
            return False

    async def run(self, ctx: AgentContext) -> AgentResult:
        try:
            import httpx
        except ImportError:
            return AgentResult(success=False, error="需要安装 httpx: pip install httpx")

        payload = {
            "prompt": ctx.get("prompt", ""),
            "workspace": str(ctx.workspace),
            "shared": {k: v for k, v in ctx.shared.items() if _is_serializable(v)},
            "last_error": ctx.get("last_error"),
            "loop_count": ctx.get("loop_count", 0),
        }

        transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0")
        async with httpx.AsyncClient(timeout=self.timeout, transport=transport) as client:
            resp = await client.post(
                self.endpoint,
                json=payload,
                headers=self.headers,
            )
            resp.raise_for_status()
            data = resp.json()

        return AgentResult(
            success=data.get("success", False),
            data=data.get("data"),
            error=data.get("error"),
            tokens_used=data.get("tokens_used", 0),
            estimated_cost=data.get("estimated_cost", 0.0),
        )


class ShellAgent(Agent):
    """通过子进程执行命令的 agent

    适合：跑测试、部署脚本、任何 CLI 工具
    命令中可以用 {workspace} 占位符引用工作区路径
    """

    def __init__(
        self,
        name: str,
        command: str,
        timeout: float = 120.0,
        cwd_workspace: bool = True,
    ):
        super().__init__(name=name, timeout=timeout)
        self.command = command
        self.cwd_workspace = cwd_workspace

    async def run(self, ctx: AgentContext) -> AgentResult:
        ws = ctx.ensure_workspace()
        cmd = self.command.replace("{workspace}", str(ws))
        cwd = str(ws) if self.cwd_workspace else None

        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await proc.communicate()
        output = stdout.decode()
        err_output = stderr.decode()

        return AgentResult(
            success=proc.returncode == 0,
            data={
                "stdout": output[-2000:],
                "stderr": err_output[-1000:] if err_output else None,
                "returncode": proc.returncode,
            },
            error=err_output[-500:] if proc.returncode != 0 else None,
        )


def _is_serializable(v: Any) -> bool:
    """检查值是否可 JSON 序列化"""
    import json
    try:
        json.dumps(v)
        return True
    except (TypeError, ValueError):
        return False
