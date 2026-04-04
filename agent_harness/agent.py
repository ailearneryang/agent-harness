"""Agent 基类和状态定义"""
from __future__ import annotations

import asyncio
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class AgentState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    RETRYING = "retrying"
    SKIPPED = "skipped"


@dataclass
class AgentResult:
    success: bool
    data: Any = None
    error: str | None = None
    duration: float = 0.0
    attempts: int = 1


@dataclass
class AgentContext:
    """Pipeline 上下文

    核心设计：
    - workspace: 共享工作区路径，agent 在这里读写文件（生产模式）
    - shared: 轻量 KV 存储，传递元信息（prompt、错误信息等）
    - history: 执行历史，harness 自动维护

    Agent 之间不直接传递代码内容。
    CodeGen 把代码写到 workspace，TestRunner 在 workspace 里跑测试。
    Harness 负责把测试错误信息通过 shared 传给 CodeGen。
    """
    pipeline_id: str = ""
    step_index: int = 0
    # 共享工作区路径
    workspace: Path = field(default_factory=lambda: Path.cwd() / ".harness_workspace")
    # 元信息传递（prompt、错误信息、配置等，不传代码内容）
    shared: dict[str, Any] = field(default_factory=dict)
    # 执行历史
    history: list[dict[str, Any]] = field(default_factory=list)

    def set(self, key: str, value: Any) -> None:
        self.shared[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.shared.get(key, default)

    def ensure_workspace(self) -> Path:
        """确保工作区目录存在"""
        self.workspace.mkdir(parents=True, exist_ok=True)
        return self.workspace


class Agent(ABC):
    """Agent 基类，所有自定义 agent 需继承此类

    Agent 的职责：
    1. 在 workspace 中完成自己的工作（生成代码、跑测试等）
    2. 返回 AgentResult 上报结果给 harness
    3. 不需要关心其他 agent，harness 负责编排和信息传递
    """

    def __init__(self, name: str | None = None, timeout: float = 300.0):
        self.name = name or self.__class__.__name__
        self.id = str(uuid.uuid4())[:8]
        self.state = AgentState.IDLE
        self.timeout = timeout  # 秒，默认 5 分钟
        self._start_time: float = 0

    @abstractmethod
    async def run(self, ctx: AgentContext) -> AgentResult:
        """执行 agent 逻辑，子类必须实现"""
        ...

    async def health_check(self) -> bool:
        """健康检查，远程 agent 可覆盖此方法"""
        return True

    async def execute(self, ctx: AgentContext) -> AgentResult:
        """内部执行入口，包装状态管理和超时控制"""
        self.state = AgentState.RUNNING
        self._start_time = time.time()
        try:
            result = await asyncio.wait_for(self.run(ctx), timeout=self.timeout)
            duration = time.time() - self._start_time
            result.duration = duration
            self.state = AgentState.SUCCESS if result.success else AgentState.FAILED
            return result
        except asyncio.TimeoutError:
            duration = time.time() - self._start_time
            self.state = AgentState.FAILED
            return AgentResult(
                success=False,
                error=f"Agent {self.name} 超时 ({self.timeout}s)",
                duration=duration,
            )
        except Exception as e:
            duration = time.time() - self._start_time
            self.state = AgentState.FAILED
            return AgentResult(success=False, error=str(e), duration=duration)

    def __repr__(self) -> str:
        return f"<Agent {self.name}({self.id}) state={self.state.value}>"
