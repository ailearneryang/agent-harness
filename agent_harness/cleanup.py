"""Workspace 自动清理"""
from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path

logger = logging.getLogger("agent_harness")

WORKSPACES_DIR = Path(".harness_workspaces")


def cleanup_old_workspaces(max_age_hours: float = 24.0, max_count: int = 50) -> int:
    """清理过期的 workspace 目录

    策略：
    1. 超过 max_age_hours 的一律删除
    2. 即使没过期，如果总数超过 max_count，删最老的
    """
    if not WORKSPACES_DIR.exists():
        return 0

    dirs = sorted(WORKSPACES_DIR.iterdir(), key=lambda d: d.stat().st_mtime)
    now = time.time()
    cutoff = now - max_age_hours * 3600
    removed = 0

    for d in dirs:
        if not d.is_dir():
            continue
        if d.stat().st_mtime < cutoff:
            shutil.rmtree(d, ignore_errors=True)
            removed += 1

    # 数量限制
    remaining = sorted(
        [d for d in WORKSPACES_DIR.iterdir() if d.is_dir()],
        key=lambda d: d.stat().st_mtime,
    )
    while len(remaining) > max_count:
        oldest = remaining.pop(0)
        shutil.rmtree(oldest, ignore_errors=True)
        removed += 1

    if removed:
        logger.info("Cleaned up %d workspace(s)", removed)
    return removed


async def cleanup_loop(interval_minutes: float = 30.0, max_age_hours: float = 24.0, max_count: int = 50):
    """后台定时清理任务"""
    while True:
        try:
            cleanup_old_workspaces(max_age_hours, max_count)
        except Exception as e:
            logger.error("Cleanup error: %s", e)
        await asyncio.sleep(interval_minutes * 60)
