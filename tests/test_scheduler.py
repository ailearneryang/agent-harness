"""Scheduler 测试"""
import asyncio
import pytest
from agent_harness.scheduler import Scheduler


async def test_submit_and_complete():
    scheduler = Scheduler(max_concurrent=2)

    async def executor(job):
        await asyncio.sleep(0.05)
        return {"success": True, "pipeline_id": "test"}

    scheduler.set_executor(executor)
    await scheduler.start()

    job = scheduler.submit("test-pipeline", "test prompt")
    assert job.status == "queued"

    await asyncio.sleep(0.3)
    assert job.status == "completed"
    await scheduler.stop()


async def test_concurrency_limit():
    """最多同时跑 max_concurrent 个 job"""
    scheduler = Scheduler(max_concurrent=2)
    running_count = 0
    max_seen = 0

    async def executor(job):
        nonlocal running_count, max_seen
        running_count += 1
        max_seen = max(max_seen, running_count)
        await asyncio.sleep(0.1)
        running_count -= 1
        return {"success": True}

    scheduler.set_executor(executor)
    await scheduler.start()

    for i in range(5):
        scheduler.submit("p", f"job{i}")

    await asyncio.sleep(0.8)
    assert max_seen <= 2
    await scheduler.stop()


async def test_failed_job():
    scheduler = Scheduler(max_concurrent=1)

    async def executor(job):
        raise RuntimeError("executor error")

    scheduler.set_executor(executor)
    await scheduler.start()

    job = scheduler.submit("p", "test")
    await asyncio.sleep(0.3)
    assert job.status == "failed"
    assert "executor error" in job.error
    await scheduler.stop()


async def test_list_jobs():
    scheduler = Scheduler(max_concurrent=1)
    scheduler.set_executor(lambda j: asyncio.sleep(0) or {"success": True})
    await scheduler.start()

    for i in range(3):
        scheduler.submit("p", f"job{i}")

    await asyncio.sleep(0.3)
    jobs = scheduler.list_jobs()
    assert len(jobs) == 3
    await scheduler.stop()
