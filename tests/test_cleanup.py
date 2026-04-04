"""Workspace 清理测试"""
import time
import pytest
from agent_harness.cleanup import cleanup_old_workspaces


def test_cleanup_by_age(tmp_path):
    # 创建 3 个目录，模拟旧的
    for i in range(3):
        d = tmp_path / f"ws_{i}"
        d.mkdir()
        # 设置修改时间为 2 天前
        old_time = time.time() - 48 * 3600
        import os
        os.utime(d, (old_time, old_time))

    import agent_harness.cleanup as cleanup_mod
    orig = cleanup_mod.WORKSPACES_DIR
    cleanup_mod.WORKSPACES_DIR = tmp_path

    removed = cleanup_old_workspaces(max_age_hours=24, max_count=100)
    assert removed == 3
    assert len(list(tmp_path.iterdir())) == 0

    cleanup_mod.WORKSPACES_DIR = orig


def test_cleanup_by_count(tmp_path):
    for i in range(10):
        d = tmp_path / f"ws_{i}"
        d.mkdir()

    import agent_harness.cleanup as cleanup_mod
    orig = cleanup_mod.WORKSPACES_DIR
    cleanup_mod.WORKSPACES_DIR = tmp_path

    removed = cleanup_old_workspaces(max_age_hours=9999, max_count=5)
    assert removed == 5
    assert len(list(tmp_path.iterdir())) == 5

    cleanup_mod.WORKSPACES_DIR = orig
