"""Workspace Git 集成 - 自动 init/commit/diff"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger("agent_harness")


async def _run_git(workspace: Path, *args: str) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=str(workspace),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout.decode(), stderr.decode()


async def git_init(workspace: Path) -> bool:
    """在 workspace 中初始化 git repo"""
    code, _, _ = await _run_git(workspace, "init")
    if code != 0:
        return False
    # 初始 .gitignore
    gitignore = workspace / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("__pycache__/\n*.pyc\n.pytest_cache/\n")
    await _run_git(workspace, "add", "-A")
    await _run_git(workspace, "commit", "-m", "initial", "--allow-empty")
    return True


async def git_commit(workspace: Path, message: str) -> bool:
    """提交当前所有变更"""
    git_dir = workspace / ".git"
    if not git_dir.exists():
        await git_init(workspace)
    await _run_git(workspace, "add", "-A")
    code, _, _ = await _run_git(workspace, "commit", "-m", message, "--allow-empty")
    return code == 0


async def git_diff(workspace: Path, ref: str = "HEAD~1") -> str | None:
    """获取与上一次 commit 的 diff"""
    git_dir = workspace / ".git"
    if not git_dir.exists():
        return None
    code, stdout, _ = await _run_git(workspace, "diff", ref, "--stat")
    if code != 0:
        return None
    # 也获取详细 diff
    _, detail, _ = await _run_git(workspace, "diff", ref)
    return f"{stdout}\n{detail}" if detail else stdout


async def git_log(workspace: Path, limit: int = 10) -> list[str]:
    """获取 commit 历史"""
    git_dir = workspace / ".git"
    if not git_dir.exists():
        return []
    code, stdout, _ = await _run_git(workspace, "log", f"--oneline", f"-{limit}")
    if code != 0:
        return []
    return [line for line in stdout.strip().split("\n") if line]
