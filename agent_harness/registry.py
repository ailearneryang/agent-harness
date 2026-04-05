"""Agent 注册中心 - 统一管理所有 agent"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_harness.agent import Agent


@dataclass
class AgentMeta:
    """Agent 元信息"""
    name: str
    agent: Agent
    description: str = ""
    category: str = "general"  # codegen / test / review / deploy / general
    config: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "config": {k: v for k, v in self.config.items() if not k.startswith("_")},
            "enabled": self.enabled,
            "state": self.agent.state.value,
            "id": self.agent.id,
            "dynamic": bool(self.config.get("_dynamic")),
        }


class AgentRegistry:
    """Agent 注册中心

    用法:
        registry = AgentRegistry()
        registry.register(my_agent, description="代码生成", category="codegen")
        agent = registry.get("CodeGen")
    """

    def __init__(self):
        self._agents: dict[str, AgentMeta] = {}

    def register(
        self,
        agent: Agent,
        description: str = "",
        category: str = "general",
        config: dict[str, Any] | None = None,
    ) -> AgentMeta:
        meta = AgentMeta(
            name=agent.name,
            agent=agent,
            description=description,
            category=category,
            config=config or {},
        )
        self._agents[agent.name] = meta
        return meta

    def unregister(self, name: str) -> bool:
        return self._agents.pop(name, None) is not None

    def get(self, name: str) -> Agent | None:
        meta = self._agents.get(name)
        return meta.agent if meta else None

    def get_meta(self, name: str) -> AgentMeta | None:
        return self._agents.get(name)

    def list_all(self) -> list[AgentMeta]:
        return list(self._agents.values())

    def list_by_category(self, category: str) -> list[AgentMeta]:
        return [m for m in self._agents.values() if m.category == category]

    def enable(self, name: str) -> bool:
        meta = self._agents.get(name)
        if meta:
            meta.enabled = True
            return True
        return False

    def disable(self, name: str) -> bool:
        meta = self._agents.get(name)
        if meta:
            meta.enabled = False
            return True
        return False

    def update_config(self, name: str, config: dict[str, Any]) -> bool:
        meta = self._agents.get(name)
        if meta:
            meta.config.update(config)
            return True
        return False

    def build_pipeline_agents(self, names: list[str]) -> list[Agent]:
        """按名称列表返回 agent 新实例，用于构建 pipeline
        
        每次构建都创建新实例，避免并发 pipeline 共享状态
        """
        import copy
        agents = []
        for n in names:
            meta = self._agents.get(n)
            if meta and meta.enabled:
                agents.append(copy.deepcopy(meta.agent))
        return agents
