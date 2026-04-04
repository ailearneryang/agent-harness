"""重试策略"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass


class RetryPolicy(ABC):
    """重试策略基类"""

    @abstractmethod
    def should_retry(self, attempt: int) -> bool: ...

    @abstractmethod
    def get_delay(self, attempt: int) -> float: ...

    async def wait(self, attempt: int) -> None:
        delay = self.get_delay(attempt)
        if delay > 0:
            await asyncio.sleep(delay)


@dataclass
class FixedRetry(RetryPolicy):
    max_retries: int = 3
    delay: float = 1.0

    def should_retry(self, attempt: int) -> bool:
        return attempt < self.max_retries

    def get_delay(self, attempt: int) -> float:
        return self.delay


@dataclass
class ExponentialBackoff(RetryPolicy):
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    multiplier: float = 2.0

    def should_retry(self, attempt: int) -> bool:
        return attempt < self.max_retries

    def get_delay(self, attempt: int) -> float:
        delay = self.base_delay * (self.multiplier ** attempt)
        return min(delay, self.max_delay)
