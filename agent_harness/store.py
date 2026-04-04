"""Pipeline 执行记录持久化 - SQLite"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DB_PATH = Path(".harness_data/harness.db")

# 线程本地连接，避免每次操作都开关连接
_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        _local.conn = conn
    return _local.conn


def init_db() -> None:
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS pipeline_runs (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            success INTEGER,
            error TEXT,
            started_at REAL NOT NULL,
            finished_at REAL,
            context_json TEXT
        );

        CREATE TABLE IF NOT EXISTS step_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pipeline_id TEXT NOT NULL,
            step_index INTEGER NOT NULL,
            agent_name TEXT NOT NULL,
            loop INTEGER DEFAULT 0,
            success INTEGER,
            error TEXT,
            data_json TEXT,
            duration REAL,
            created_at REAL NOT NULL,
            FOREIGN KEY (pipeline_id) REFERENCES pipeline_runs(id)
        );

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pipeline_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            agent_name TEXT NOT NULL,
            step_index INTEGER,
            data_json TEXT,
            created_at REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_steps_pipeline ON step_results(pipeline_id);
        CREATE INDEX IF NOT EXISTS idx_events_pipeline ON events(pipeline_id);

        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            pipeline_name TEXT NOT NULL,
            prompt TEXT,
            status TEXT DEFAULT 'queued',
            error TEXT,
            created_at REAL NOT NULL,
            started_at REAL,
            finished_at REAL,
            result_json TEXT
        );

        CREATE TABLE IF NOT EXISTS checkpoints (
            pipeline_id TEXT PRIMARY KEY,
            resume_from_step INTEGER NOT NULL,
            context_json TEXT NOT NULL,
            created_at REAL NOT NULL
        );
    """)


class Store:
    """持久化存储"""

    def __init__(self):
        init_db()

    def save_pipeline_start(self, pipeline_id: str, name: str, context: dict | None = None) -> None:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO pipeline_runs (id, name, started_at, context_json) VALUES (?, ?, ?, ?)",
            (pipeline_id, name, time.time(), json.dumps(context or {})),
        )
        conn.commit()

    def save_pipeline_end(self, pipeline_id: str, success: bool, error: str | None = None) -> None:
        conn = _get_conn()
        conn.execute(
            "UPDATE pipeline_runs SET success=?, error=?, finished_at=? WHERE id=?",
            (int(success), error, time.time(), pipeline_id),
        )
        conn.commit()

    def save_step(
        self, pipeline_id: str, step_index: int, agent_name: str,
        success: bool, error: str | None, data: Any, duration: float, loop: int = 0,
    ) -> None:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO step_results (pipeline_id, step_index, agent_name, loop, success, error, data_json, duration, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pipeline_id, step_index, agent_name, loop, int(success), error, json.dumps(data), duration, time.time()),
        )
        conn.commit()

    def save_event(self, pipeline_id: str, event_type: str, agent_name: str, step_index: int, data: dict) -> None:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO events (pipeline_id, event_type, agent_name, step_index, data_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (pipeline_id, event_type, agent_name, step_index, json.dumps(data), time.time()),
        )
        conn.commit()

    def list_runs(self, limit: int = 50) -> list[dict]:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_run(self, pipeline_id: str) -> dict | None:
        conn = _get_conn()
        row = conn.execute("SELECT * FROM pipeline_runs WHERE id=?", (pipeline_id,)).fetchone()
        if not row:
                return None
        run = dict(row)
        run["steps"] = [
            dict(r) for r in conn.execute(
                "SELECT * FROM step_results WHERE pipeline_id=? ORDER BY id", (pipeline_id,)
            ).fetchall()
        ]
        run["events"] = [
            dict(r) for r in conn.execute(
                "SELECT * FROM events WHERE pipeline_id=? ORDER BY id", (pipeline_id,)
            ).fetchall()
        ]
        return run

    # --- Checkpoint ---
    def save_checkpoint(self, pipeline_id: str, resume_from_step: int, context: dict) -> None:
        conn = _get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO checkpoints (pipeline_id, resume_from_step, context_json, created_at) VALUES (?, ?, ?, ?)",
            (pipeline_id, resume_from_step, json.dumps(context), time.time()),
        )
        conn.commit()

    def get_checkpoint(self, pipeline_id: str) -> dict | None:
        conn = _get_conn()
        row = conn.execute("SELECT * FROM checkpoints WHERE pipeline_id=?", (pipeline_id,)).fetchone()
        if not row:
            return None
        return {
            "pipeline_id": row["pipeline_id"],
            "resume_from_step": row["resume_from_step"],
            "context": json.loads(row["context_json"]),
        }

    def delete_checkpoint(self, pipeline_id: str) -> None:
        conn = _get_conn()
        conn.execute("DELETE FROM checkpoints WHERE pipeline_id=?", (pipeline_id,))
        conn.commit()
