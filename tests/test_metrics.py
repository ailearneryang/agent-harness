"""Metrics 测试"""
import pytest
from agent_harness.metrics import MetricsCollector, AlertRule


def test_counter():
    m = MetricsCollector()
    m.inc("runs")
    m.inc("runs")
    m.inc("runs", 3)
    assert m._counters["runs"] == 5


def test_counter_with_labels():
    m = MetricsCollector()
    m.inc("duration", agent="CodeGen")
    m.inc("duration", agent="TestRunner")
    assert m._counters["duration{agent=CodeGen}"] == 1
    assert m._counters["duration{agent=TestRunner}"] == 1


def test_histogram():
    m = MetricsCollector()
    for v in [1.0, 2.0, 3.0, 4.0, 5.0]:
        m.observe("latency", v)
    snap = m.snapshot()
    h = snap["histograms"]["latency"]
    assert h["count"] == 5
    assert h["avg"] == 3.0
    assert h["min"] == 1.0
    assert h["max"] == 5.0


def test_alert_fires():
    m = MetricsCollector()

    def always_fire(collector):
        return {"alert": "test", "message": "fired"}

    m.add_alert(AlertRule(name="test", description="test", evaluate_fn=always_fire))
    alerts = m.check_alerts()
    assert len(alerts) == 1
    assert alerts[0]["alert"] == "test"


def test_alert_no_fire():
    m = MetricsCollector()

    def never_fire(collector):
        return None

    m.add_alert(AlertRule(name="test", description="test", evaluate_fn=never_fire))
    alerts = m.check_alerts()
    assert len(alerts) == 0
