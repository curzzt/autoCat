from __future__ import annotations

import sqlite3
import threading
from typing import List, Optional

from .config import settings
from .models import Job, JobStatus, now_iso

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(settings.database_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db() -> None:
    with _lock, _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                input_url TEXT,
                source TEXT NOT NULL DEFAULT 'url',
                status TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                message TEXT,
                error TEXT,
                warnings TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )


def _row_to_job(row: sqlite3.Row) -> Job:
    import json

    return Job(
        id=row["id"],
        input_url=row["input_url"],
        source=row["source"],
        status=JobStatus(row["status"]),
        progress=row["progress"],
        message=row["message"] or "",
        error=row["error"],
        warnings=json.loads(row["warnings"] or "[]"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def create_job(job: Job) -> Job:
    import json

    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO jobs (id, input_url, source, status, progress, message,
                              error, warnings, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.id,
                job.input_url,
                job.source,
                job.status.value,
                job.progress,
                job.message,
                job.error,
                json.dumps(job.warnings, ensure_ascii=False),
                job.created_at,
                job.updated_at,
            ),
        )
    return job


def save_job(job: Job) -> None:
    import json

    job.updated_at = now_iso()
    with _lock, _connect() as conn:
        conn.execute(
            """
            UPDATE jobs
            SET input_url = ?, source = ?, status = ?, progress = ?, message = ?,
                error = ?, warnings = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                job.input_url,
                job.source,
                job.status.value,
                job.progress,
                job.message,
                job.error,
                json.dumps(job.warnings, ensure_ascii=False),
                job.updated_at,
                job.id,
            ),
        )


def get_job(job_id: str) -> Optional[Job]:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job(row) if row else None


def list_jobs(limit: int = 50) -> List[Job]:
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_job(row) for row in rows]
