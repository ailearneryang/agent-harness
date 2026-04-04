"""状态监控和可观测性"""
from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from agent_harness.agent import Agent, AgentResult, AgentState

logger = logging.getLogger("agent_harness")


@dataclass
class Event:
    timestamp: float
    event_type: str
    agent_name: str
    step_index: int
    pipeline_id: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "ts": self.timestamp,
            "type": self.event_type,
            "agent": self.agent_name,
            "step": self.step_index,
            "pipeline": self.pipeline_id,
            **self.data,
        }


class Monitor(ABC):
    """监控基类，可扩展为 Prometheus / OpenTelemetry 等"""

    @abstractmethod
    def on_event(self, event: Event) -> None: ...

    def emit(
        self,
        event_type: str,
        agent: Agent,
        step_index: int,
        pipeline_id: str,
        **extra: Any,
    ) -> None:
        self.on_event(
            Event(
                timestamp=time.time(),
                event_type=event_type,
                agent_name=agent.name,
                step_index=step_index,
                pipeline_id=pipeline_id,
                data=extra,
            )
        )


class ConsoleMonitor(Monitor):
    """将事件输出到控制台/日志，同时持久化到 SQLite"""

    def __init__(self, use_json: bool = False, persist: bool = True):
        self.use_json = use_json
        self.events: list[Event] = []
        self._store = None
        if persist:
            from agent_harness.store import Store
            self._store = Store()

    def on_event(self, event: Event) -> None:
        self.events.append(event)
        if self.use_json:
            logger.info(json.dumps(event.to_dict()))
        else:
            logger.info(
                "[%s] %s | agent=%s step=%d | %s",
                event.pipeline_id[:8],
                event.event_type.upper(),
                event.agent_name,
                event.step_index,
                event.data,
            )
        # 持久化
        if self._store:
            self._store.save_event(
                event.pipeline_id, event.event_type,
                event.agent_name, event.step_index, event.data,
            )

    def summary(self) -> dict[str, Any]:
        """返回 pipeline 执行摘要"""
        return {
            "total_events": len(self.events),
            "events": [e.to_dict() for e in self.events],
        }
