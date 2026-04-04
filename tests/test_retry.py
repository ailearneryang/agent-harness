"""重试策略测试"""
import pytest
from agent_harness.retry import FixedRetry, ExponentialBackoff


def test_fixed_retry_should_retry():
    r = FixedRetry(max_retries=3, delay=0)
    assert r.should_retry(0) is True
    assert r.should_retry(2) is True
    assert r.should_retry(3) is False


def test_fixed_retry_delay():
    r = FixedRetry(max_retries=3, delay=1.5)
    assert r.get_delay(0) == 1.5
    assert r.get_delay(2) == 1.5


def test_exponential_backoff():
    r = ExponentialBackoff(max_retries=4, base_delay=1.0, multiplier=2.0, max_delay=10.0)
    assert r.get_delay(0) == 1.0
    assert r.get_delay(1) == 2.0
    assert r.get_delay(2) == 4.0
    assert r.get_delay(3) == 8.0


def test_exponential_backoff_max_delay():
    r = ExponentialBackoff(max_retries=10, base_delay=1.0, multiplier=2.0, max_delay=5.0)
    assert r.get_delay(10) == 5.0


def test_exponential_backoff_should_retry():
    r = ExponentialBackoff(max_retries=3)
    assert r.should_retry(2) is True
    assert r.should_retry(3) is False
