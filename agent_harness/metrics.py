"""指标收集和告警"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from agent_harness.store import Store


@dataclass
class MetricPoint:
    name: str
    value: float
    labels: dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class MetricsCollector:
    """收集 pipeline 和 agent 执行指标

    指标包括：
    - pipeline_runs_total: pipeline 执行总数
    - pipeline_success_rate: 成功率
    - agent_duration_seconds: agent 执行耗时
    - agent_failure_total: agent 失败次数
    - loop_back_total: 回退循环次数
    """

    def __init__(self):
        self._counters: dict[str, float] = {}
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = {}
        self._alerts: list[AlertRule] = []

    def inc(self, name: str, value: float = 1.0, **labels: str) -> None:
        key = self._key(name, labels)
        self._counters[key] = self._counters.get(key, 0) + value

    def set_gauge(self, name: str, value: float, **labels: str) -> None:
        key = self._key(name, labels)
        self._gauges[key] = value

    def observe(self, name: str, value: float, **labels: str) -> None:
        key = self._key(name, labels)
        if key not in self._histograms:
            self._histograms[key] = []
        self._histograms[key].append(value)

    def add_alert(self, rule: "AlertRule") -> None:
        self._alerts.append(rule)

    def check_alerts(self) -> list[dict]:
        """检查所有告警规则，返回触发的告警"""
        fired = []
        for rule in self._alerts:
            result = rule.evaluate(self)
            if result:
                fired.append(result)
        return fired

    def snapshot(self) -> dict[str, Any]:
        """返回当前所有指标快照"""
        hist_summary = {}
        for key, values in self._histograms.items():
            if values:
                hist_summary[key] = {
                    "count": len(values),
                    "avg": sum(values) / len(values),
                    "min": min(values),
                    "max": max(values),
                    "p95": sorted(values)[int(len(values) * 0.95)] if len(values) > 1 else values[0],
                }
        return {
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
            "histograms": hist_summary,
        }

    def compute_from_store(self, store: Store) -> dict[str, Any]:
        """从持久化数据计算汇总指标"""
        runs = store.list_runs(limit=1000)
        total = len(runs)
        success = sum(1 for r in runs if r.get("success"))
        failed = total - success

        durations = []
        for r in runs:
            if r.get("started_at") and r.get("finished_at"):
                durations.append(r["finished_at"] - r["started_at"])

        return {
            "pipeline_runs_total": total,
            "pipeline_success": success,
            "pipeline_failed": failed,
            "pipeline_success_rate": round(success / total * 100, 1) if total > 0 else 0,
            "avg_duration": round(sum(durations) / len(durations), 2) if durations else 0,
            "max_duration": round(max(durations), 2) if durations else 0,
        }

    @staticmethod
    def _key(name: str, labels: dict) -> str:
        if not labels:
            return name
        label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        return f"{name}{{{label_str}}}"


@dataclass
class AlertRule:
    """告警规则"""
    name: str
    description: str
    # 评估函数：接收 MetricsCollector，返回 None（正常）或 dict（告警）
    evaluate_fn: Callable[["MetricsCollector"], dict | None] = field(default=None)

    def evaluate(self, collector: "MetricsCollector") -> dict | None:
        if self.evaluate_fn:
            return self.evaluate_fn(collector)
        return None


# 内置告警规则
def high_failure_rate_alert(threshold: float = 50.0) -> AlertRule:
    """成功率低于阈值时告警"""
    def _eval(collector: MetricsCollector) -> dict | None:
        store = Store()
        metrics = collector.compute_from_store(store)
        rate = metrics.get("pipeline_success_rate", 100)
        if rate < threshold and metrics["pipeline_runs_total"] >= 3:
            return {
                "alert": "high_failure_rate",
                "message": f"Pipeline 成功率 {rate}% 低于阈值 {threshold}%",
                "current_rate": rate,
                "threshold": threshold,
            }
        return None
    return AlertRule(name="high_failure_rate", description=f"成功率低于 {threshold}%", evaluate_fn=_eval)


def slow_pipeline_alert(threshold_seconds: float = 60.0) -> AlertRule:
    """Pipeline 平均耗时超过阈值时告警"""
    def _eval(collector: MetricsCollector) -> dict | None:
        store = Store()
        metrics = collector.compute_from_store(store)
        avg = metrics.get("avg_duration", 0)
        if avg > threshold_seconds and metrics["pipeline_runs_total"] >= 3:
            return {
                "alert": "slow_pipeline",
                "message": f"Pipeline 平均耗时 {avg}s 超过阈值 {threshold_seconds}s",
                "avg_duration": avg,
                "threshold": threshold_seconds,
            }
        return None
    return AlertRule(name="slow_pipeline", description=f"平均耗时超过 {threshold_seconds}s", evaluate_fn=_eval)
