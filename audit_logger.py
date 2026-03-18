from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List


class AuditLogger:
    def __init__(self, db_path: str):
        self.db_path = str(Path(db_path))
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS audit_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts REAL NOT NULL,
                        role TEXT NOT NULL,
                        client_ip TEXT NOT NULL,
                        command TEXT NOT NULL,
                        payload TEXT,
                        result TEXT NOT NULL
                    )
                    """
                )
                conn.commit()
            finally:
                conn.close()

    def log_event(
        self,
        role: str,
        client_ip: str,
        command: str,
        payload: Dict[str, Any] | None,
        result: str,
    ):
        payload_json = json.dumps(payload or {}, separators=(",", ":"))
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO audit_events (ts, role, client_ip, command, payload, result)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (time.time(), role, client_ip, command, payload_json, result),
                )

                # Keep the table bounded to prevent unbounded storage growth.
                conn.execute(
                    """
                    DELETE FROM audit_events
                    WHERE id NOT IN (
                        SELECT id FROM audit_events ORDER BY id DESC LIMIT 50000
                    )
                    """
                )
                conn.commit()
            finally:
                conn.close()

    def recent_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 500))
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """
                    SELECT id, ts, role, client_ip, command, payload, result
                    FROM audit_events
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (safe_limit,),
                )
                rows = cursor.fetchall()
            finally:
                conn.close()

        events: List[Dict[str, Any]] = []
        for row in rows:
            payload = row["payload"]
            try:
                payload_obj = json.loads(payload) if payload else {}
            except json.JSONDecodeError:
                payload_obj = {"raw": payload}

            events.append(
                {
                    "id": row["id"],
                    "ts": row["ts"],
                    "role": row["role"],
                    "client_ip": row["client_ip"],
                    "command": row["command"],
                    "payload": payload_obj,
                    "result": row["result"],
                }
            )
        return events
