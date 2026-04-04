"""多租户支持 - 每个租户有独立的 registry、pipeline 配置和数据隔离"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_harness.registry import AgentRegistry


@dataclass
class Tenant:
    id: str
    name: str
    token: str  # 每个租户独立的 API token
    pipeline_dir: str = ""  # 租户专属 pipeline 配置目录
    workspace_prefix: str = ""  # workspace 路径前缀
    registry: AgentRegistry = field(default_factory=AgentRegistry)
    config: dict[str, Any] = field(default_factory=dict)

    def get_workspace_base(self) -> Path:
        prefix = self.workspace_prefix or f".harness_workspaces/{self.id}"
        return Path(prefix)

    def get_pipeline_dir(self) -> Path:
        return Path(self.pipeline_dir or f"pipelines/{self.id}")


class TenantRegistry:
    """租户注册中心"""

    def __init__(self):
        self._tenants: dict[str, Tenant] = {}
        self._token_map: dict[str, str] = {}  # token -> tenant_id

    def register(self, tenant: Tenant) -> None:
        self._tenants[tenant.id] = tenant
        self._token_map[tenant.token] = tenant.id

    def get_by_token(self, token: str) -> Tenant | None:
        tid = self._token_map.get(token)
        return self._tenants.get(tid) if tid else None

    def get(self, tenant_id: str) -> Tenant | None:
        return self._tenants.get(tenant_id)

    def list_all(self) -> list[Tenant]:
        return list(self._tenants.values())
