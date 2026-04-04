"""Pipeline 队列和调度"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("agent_harness")


@dataclass
class Job:
    id: str
    pipeline_name: str
    prompt: str
    config: dict[str, Any] = field(default_factory=dict)
    status: str = "queued"  # queued / running / completed / failed
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    result: dict | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "pipeline_name": self.pipeline_name,
            "prompt": self.prompt,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }


class Scheduler:
    """Pipeline 调度器

    支持：
    - 任务队列：多个 pipeline 请求排队执行
    - 并发控制：限制同时运行的 pipeline 数量
    - 任务状态查询
    """

    def __init__(self, max_concurrent: int = 2):
        self.max_concurrent = max_concurrent
        self._queue: asyncio.Queue[Job] = asyncio.Queue()
        self._jobs: dict[str, Job] = {}
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._running = False
        self._worker_task: asyncio.Task | None = None
        # 外部注入的执行函数
        self._execute_fn = None

    def set_executor(self, fn):
        """设置 pipeline 执行函数: async fn(job) -> dict"""
        self._execute_fn = fn

    async def start(self):
        """启动调度器后台 worker"""
        if self._running:
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop())
        logger.info("Scheduler started (max_concurrent=%d)", self.max_concurrent)

    async def stop(self, wait_for_running: bool = True):
        self._running = False
        if wait_for_running:
            # 等待所有正在运行的 job 完成（最多等 60s）
            running_jobs = [j for j in self._jobs.values() if j.status == "running"]
            if running_jobs:
                logger.info("Waiting for %d running job(s) to complete...", len(running_jobs))
                for _ in range(60):
                    await asyncio.sleep(1)
                    if not any(j.status == "running" for j in self._jobs.values()):
                        break
        if self._worker_task:
            self._worker_task.cancel()

    def submit(self, pipeline_name: str, prompt: str, **config) -> Job:
        """提交一个 pipeline 任务到队列"""
        job = Job(
            id=uuid.uuid4().hex[:12],
            pipeline_name=pipeline_name,
            prompt=prompt,
            config=config,
        )
        self._jobs[job.id] = job
        self._queue.put_nowait(job)
        logger.info("Job %s queued: %s", job.id, pipeline_name)
        return job

    def get_job(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list_jobs(self, limit: int = 50) -> list[Job]:
        jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    async def _worker_loop(self):
        """后台 worker，从队列取任务执行"""
        while self._running:
            try:
                job = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            asyncio.create_task(self._run_job(job))

    async def _run_job(self, job: Job):
        async with self._semaphore:
            job.status = "running"
            job.started_at = time.time()
            logger.info("Job %s started", job.id)

            try:
                if self._execute_fn:
                    result = await self._execute_fn(job)
                    job.result = result
                    job.status = "completed" if result.get("success") else "failed"
                    job.error = result.get("error")
                else:
                    job.status = "failed"
                    job.error = "No executor configured"
            except Exception as e:
                job.status = "failed"
                job.error = str(e)
                logger.error("Job %s failed: %s", job.id, e)
            finally:
                job.finished_at = time.time()
                logger.info("Job %s finished: %s", job.id, job.status)
