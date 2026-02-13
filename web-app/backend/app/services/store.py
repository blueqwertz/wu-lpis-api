import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

DB_PATH = Path(__file__).resolve().parents[2] / "jobs.db"


class JobStore:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    result_json TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(job_id)
                )
                """
            )

    def create_job(self, job_id: str, request_data: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO jobs (job_id, status, created_at, updated_at, request_json) VALUES (?, ?, ?, ?, ?)",
                (job_id, "queued", now, now, json.dumps(request_data)),
            )
        return self.get_job(job_id)

    def update_job(self, job_id: str, *, status: str | None = None, result: dict[str, Any] | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            if status is not None and result is not None:
                conn.execute(
                    "UPDATE jobs SET status = ?, result_json = ?, updated_at = ? WHERE job_id = ?",
                    (status, json.dumps(result), now, job_id),
                )
            elif status is not None:
                conn.execute("UPDATE jobs SET status = ?, updated_at = ? WHERE job_id = ?", (status, now, job_id))
            elif result is not None:
                conn.execute(
                    "UPDATE jobs SET result_json = ?, updated_at = ? WHERE job_id = ?",
                    (json.dumps(result), now, job_id),
                )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if not row:
                return None
            return {
                "job_id": row["job_id"],
                "status": row["status"],
                "created_at": datetime.fromisoformat(row["created_at"]),
                "updated_at": datetime.fromisoformat(row["updated_at"]),
                "request": json.loads(row["request_json"]),
                "result": json.loads(row["result_json"]) if row["result_json"] else None,
            }

    def append_log(self, job_id: str, level: str, message: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO logs (job_id, level, message, created_at) VALUES (?, ?, ?, ?)",
                (job_id, level, message, now),
            )
            log_id = cur.lastrowid
        return {
            "id": log_id,
            "job_id": job_id,
            "level": level,
            "message": message,
            "created_at": datetime.fromisoformat(now),
        }

    def get_logs(self, job_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM logs WHERE job_id = ? ORDER BY id ASC", (job_id,)).fetchall()
        return [
            {
                "id": row["id"],
                "job_id": row["job_id"],
                "level": row["level"],
                "message": row["message"],
                "created_at": datetime.fromisoformat(row["created_at"]),
            }
            for row in rows
        ]
