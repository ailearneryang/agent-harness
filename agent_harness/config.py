"""配置化 Pipeline - 从 YAML/dict 构建 pipeline"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from agent_harness.pipeline import Pipeline
from agent_harness.registry import AgentRegistry
from agent_harness.retry import ExponentialBackoff, FixedRetry, RetryPolicy


def load_pipeline_config(path: str | Path) -> dict:
    """从 YAML 文件加载 pipeline 配置"""
    with open(path) as f:
        return yaml.safe_load(f)


def build_pipeline_from_config(config: dict, registry: AgentRegistry) -> Pipeline:
    """从配置字典构建 pipeline

    配置格式:
    ```yaml
    name: dev-test-deploy
    steps:
      - agent: CodeGen
      - agent: TestRunner
        on_fail_goto: CodeGen
        max_loops: 3
      - agent: Reviewer
        condition: prev_success
      - agent: Deployer
    ```
    """
    pipeline = Pipeline(name=config.get("name", "pipeline"))

    for step_cfg in config.get("steps", []):
        agent_name = step_cfg["agent"]

        # 支持并行组
        if isinstance(agent_name, list):
            agents = []
            for name in agent_name:
                agent = registry.get(name)
                if agent:
                    agents.append(agent)
            if agents:
                pipeline.add_parallel(agents, abort_on_fail=step_cfg.get("abort_on_fail", True))
            continue

        agent = registry.get(agent_name)
        if not agent:
            raise ValueError(f"Agent '{agent_name}' 未注册")

        retry = _build_retry(step_cfg.get("retry"))
        condition = _build_condition(step_cfg.get("condition"))

        pipeline.add(
            agent,
            retry=retry,
            condition=condition,
            abort_on_fail=step_cfg.get("abort_on_fail", True),
            on_fail_goto=step_cfg.get("on_fail_goto"),
            max_loops=step_cfg.get("max_loops", 3),
            approval=step_cfg.get("approval", False),
            approval_message=step_cfg.get("approval_message", ""),
        )

    return pipeline


def _build_retry(cfg: dict | None) -> RetryPolicy | None:
    if not cfg:
        return None
    strategy = cfg.get("strategy", "fixed")
    if strategy == "exponential":
        return ExponentialBackoff(
            max_retries=cfg.get("max_retries", 3),
            base_delay=cfg.get("base_delay", 1.0),
            max_delay=cfg.get("max_delay", 60.0),
        )
    return FixedRetry(
        max_retries=cfg.get("max_retries", 3),
        delay=cfg.get("delay", 1.0),
    )


def _build_condition(condition_str: str | None):
    """从字符串构建条件函数"""
    if not condition_str:
        return None
    if condition_str == "prev_success":
        return lambda ctx, prev: prev is not None and prev.get("success", False)
    if condition_str == "prev_failed":
        return lambda ctx, prev: prev is not None and not prev.get("success", True)
    if condition_str == "always":
        return None
    return None
