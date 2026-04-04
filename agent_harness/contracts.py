"""Agent 输入输出契约 - 类型安全的上下文管理"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentIO:
    """声明 agent 的输入输出 key 和类型

    用法：
        class CodeGenAgent(Agent):
            io = AgentIO(
                inputs={"prompt": str},
                outputs={"generated_code": str, "code_language": str},
            )
    """
    inputs: dict[str, type] = field(default_factory=dict)
    outputs: dict[str, type] = field(default_factory=dict)

    def validate_inputs(self, shared: dict[str, Any]) -> list[str]:
        """校验输入是否满足"""
        errors = []
        for key, expected_type in self.inputs.items():
            if key not in shared:
                errors.append(f"缺少输入: {key} ({expected_type.__name__})")
            elif not isinstance(shared[key], expected_type):
                errors.append(
                    f"类型错误: {key} 期望 {expected_type.__name__}，"
                    f"实际 {type(shared[key]).__name__}"
                )
        return errors

    def validate_outputs(self, shared: dict[str, Any]) -> list[str]:
        """校验输出是否满足"""
        errors = []
        for key, expected_type in self.outputs.items():
            if key not in shared:
                errors.append(f"缺少输出: {key} ({expected_type.__name__})")
        return errors
