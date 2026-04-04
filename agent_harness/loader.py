"""插件式 Agent 加载 - 从 YAML 配置动态加载 agent"""
from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import yaml

from agent_harness.agent import Agent
from agent_harness.registry import AgentRegistry


def load_agents_from_config(path: str | Path, registry: AgentRegistry) -> list[str]:
    """从 YAML 配置文件加载并注册 agent

    配置格式 (agents.yaml):
    ```yaml
    agents:
      - name: CodeGen
        module: agent_harness.agents.codegen
        class: CodeGenAgent
        description: 代码生成
        category: codegen
        config:
          mock: true

      - name: MyRemoteAgent
        type: remote
        endpoint: http://localhost:9000/run
        description: 远程 agent
        category: general
        config:
          headers:
            Authorization: Bearer ${YOUR_API_KEY}

      - name: Linter
        type: shell
        command: "python3 -m pylint {workspace}/solution.py"
        description: 代码检查
        category: review
    ```
    """
    with open(path) as f:
        cfg = yaml.safe_load(f)

    loaded = []
    for agent_cfg in cfg.get("agents", []):
        agent = _create_agent(agent_cfg)
        if agent:
            registry.register(
                agent,
                description=agent_cfg.get("description", ""),
                category=agent_cfg.get("category", "general"),
                config=agent_cfg.get("config", {}),
            )
            loaded.append(agent.name)

    return loaded


def _create_agent(cfg: dict) -> Agent | None:
    """根据配置创建 agent 实例"""
    agent_type = cfg.get("type", "class")
    name = cfg.get("name", "unnamed")
    timeout = cfg.get("timeout", 300.0)

    if agent_type == "remote":
        from agent_harness.agents.remote import RemoteAgent
        headers = cfg.get("config", {}).get("headers", {})
        return RemoteAgent(
            name=name,
            endpoint=cfg["endpoint"],
            headers=headers,
            timeout=timeout,
            health_endpoint=cfg.get("health_endpoint"),
        )

    if agent_type == "shell":
        from agent_harness.agents.remote import ShellAgent
        return ShellAgent(
            name=name,
            command=cfg["command"],
            timeout=timeout,
        )

    # type == "class": 从 module 动态加载
    module_path = cfg.get("module")
    class_name = cfg.get("class")
    if not module_path or not class_name:
        return None

    try:
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        init_kwargs = cfg.get("config", {})
        init_kwargs["name"] = name
        if "timeout" not in init_kwargs:
            init_kwargs["timeout"] = timeout
        return cls(**init_kwargs)
    except Exception as e:
        import logging
        logging.getLogger("agent_harness").error("加载 agent %s 失败: %s", name, e)
        return None
