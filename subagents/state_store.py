#!/usr/bin/env python3
"""SQLite-backed run/task/event state store for subagent scheduler."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


class StateStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            plan_hash TEXT,
            config_json TEXT,
            summary_path TEXT,
            error_message TEXT
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            agent_name TEXT NOT NULL,
            attempt INTEGER NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            latency_ms INTEGER,
            input_hash TEXT,
            output_hash TEXT,
            output_path TEXT,
            error_code TEXT,
            error_message TEXT
        );

        CREATE TABLE IF NOT EXISTS artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            agent_name TEXT,
            kind TEXT NOT NULL,
            path TEXT NOT NULL,
            content_hash TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            task_id INTEGER,
            level TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload_json TEXT,
            created_at TEXT NOT NULL
        );
        """
        with self._lock:
            self.conn.executescript(schema)
            self.conn.commit()

    @staticmethod
    def _now() -> str:
        return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    def create_run(self, run_id: str, status: str, plan_hash: str, config: Dict[str, Any]) -> None:
        with self._lock:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO runs(run_id, status, started_at, plan_hash, config_json)
                VALUES(?, ?, ?, ?, ?)
                """,
                (run_id, status, self._now(), plan_hash, json.dumps(config, ensure_ascii=False)),
            )
            self.conn.commit()

    def update_run(self, run_id: str, status: str, summary_path: Optional[str] = None, error: str = "") -> None:
        with self._lock:
            self.conn.execute(
                """
                UPDATE runs
                SET status = ?, ended_at = ?, summary_path = COALESCE(?, summary_path), error_message = ?
                WHERE run_id = ?
                """,
                (status, self._now(), summary_path, error, run_id),
            )
            self.conn.commit()

    def start_task(self, run_id: str, agent_name: str, attempt: int, input_hash: str) -> int:
        with self._lock:
            cur = self.conn.execute(
                """
                INSERT INTO tasks(run_id, agent_name, attempt, status, started_at, input_hash)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (run_id, agent_name, attempt, "RUNNING", self._now(), input_hash),
            )
            self.conn.commit()
            return int(cur.lastrowid)

    def finish_task(
        self,
        task_id: int,
        status: str,
        latency_ms: int,
        output_hash: str = "",
        output_path: str = "",
        error_code: str = "",
        error_message: str = "",
    ) -> None:
        with self._lock:
            self.conn.execute(
                """
                UPDATE tasks
                SET status = ?, ended_at = ?, latency_ms = ?, output_hash = ?, output_path = ?,
                    error_code = ?, error_message = ?
                WHERE id = ?
                """,
                (
                    status,
                    self._now(),
                    latency_ms,
                    output_hash,
                    output_path,
                    error_code,
                    error_message,
                    task_id,
                ),
            )
            self.conn.commit()

    def log_event(
        self,
        run_id: str,
        level: str,
        event_type: str,
        payload: Dict[str, Any],
        task_id: Optional[int] = None,
    ) -> None:
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO events(run_id, task_id, level, event_type, payload_json, created_at)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    task_id,
                    level,
                    event_type,
                    json.dumps(payload, ensure_ascii=False),
                    self._now(),
                ),
            )
            self.conn.commit()

    def record_artifact(
        self,
        run_id: str,
        kind: str,
        path: str,
        content_hash: str,
        agent_name: Optional[str] = None,
    ) -> None:
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO artifacts(run_id, agent_name, kind, path, content_hash, created_at)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (run_id, agent_name, kind, path, content_hash, self._now()),
            )
            self.conn.commit()

    def close(self) -> None:
        with self._lock:
            self.conn.close()
