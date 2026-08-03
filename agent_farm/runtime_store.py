from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


ACTIVE_JOB_STATUSES = ("QUEUED", "RUNNING")
RUNTIME_SCHEMA_VERSION = 2


class RuntimeStore:
    """Durable storage for desktop planning/farm jobs and their ordered events."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > RUNTIME_SCHEMA_VERSION:
                raise RuntimeError(
                    f"Runtime database schema {version} is newer than supported "
                    f"schema {RUNTIME_SCHEMA_VERSION}."
                )
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runtime_jobs (
                    kind TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    correlation_id TEXT,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (kind, job_id)
                );

                CREATE INDEX IF NOT EXISTS runtime_jobs_recent
                    ON runtime_jobs (kind, created_at DESC);

                CREATE TABLE IF NOT EXISTS runtime_events (
                    kind TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    correlation_id TEXT,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (kind, job_id, sequence),
                    FOREIGN KEY (kind, job_id)
                        REFERENCES runtime_jobs (kind, job_id)
                        ON DELETE CASCADE
                );
                """
            )
            self._ensure_column(connection, "runtime_jobs", "correlation_id", "TEXT")
            self._ensure_column(connection, "runtime_events", "correlation_id", "TEXT")
            connection.executescript(
                """
                CREATE INDEX IF NOT EXISTS runtime_jobs_correlation
                    ON runtime_jobs (correlation_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS runtime_events_correlation
                    ON runtime_events (correlation_id, sequence ASC);
                """
            )
            connection.execute(f"PRAGMA user_version = {RUNTIME_SCHEMA_VERSION}")

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        declaration: str,
    ) -> None:
        columns = {
            str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    @staticmethod
    def _encode(payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))

    @staticmethod
    def _decode(raw: str) -> dict[str, Any]:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("Runtime payload must be a JSON object.")
        return payload

    def create_job(self, kind: str, job: dict[str, Any]) -> None:
        job_id = str(job["job_id"])
        status = str(job["status"])
        created_at = str(job["created_at"])
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runtime_jobs
                    (kind, job_id, status, created_at, updated_at, correlation_id, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    kind,
                    job_id,
                    status,
                    created_at,
                    created_at,
                    job.get("correlation_id"),
                    self._encode(job),
                ),
            )

    def update_job(
        self,
        kind: str,
        job_id: str,
        changes: dict[str, Any],
        *,
        updated_at: str,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload_json FROM runtime_jobs WHERE kind = ? AND job_id = ?",
                (kind, job_id),
            ).fetchone()
            if row is None:
                raise FileNotFoundError(f"Unknown {kind} job: {job_id}")
            job = self._decode(row["payload_json"])
            job.update(changes)
            connection.execute(
                """
                UPDATE runtime_jobs
                SET status = ?, updated_at = ?, payload_json = ?
                WHERE kind = ? AND job_id = ?
                """,
                (str(job["status"]), updated_at, self._encode(job), kind, job_id),
            )
            return job

    def get_job(self, kind: str, job_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM runtime_jobs WHERE kind = ? AND job_id = ?",
                (kind, job_id),
            ).fetchone()
        if row is None:
            raise FileNotFoundError(f"Unknown {kind} job: {job_id}")
        return self._decode(row["payload_json"])

    def recent_jobs(self, kind: str, *, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM runtime_jobs
                WHERE kind = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (kind, limit),
            ).fetchall()
        return [self._decode(row["payload_json"]) for row in rows]

    def append_event(
        self,
        kind: str,
        job_id: str,
        event: dict[str, Any],
    ) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            job = connection.execute(
                "SELECT correlation_id FROM runtime_jobs WHERE kind = ? AND job_id = ?",
                (kind, job_id),
            ).fetchone()
            if job is None:
                raise FileNotFoundError(f"Unknown {kind} job: {job_id}")
            row = connection.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) AS sequence
                FROM runtime_events
                WHERE kind = ? AND job_id = ?
                """,
                (kind, job_id),
            ).fetchone()
            payload = dict(event)
            payload["sequence"] = int(row["sequence"]) + 1
            correlation_id = payload.get("correlation_id") or job["correlation_id"]
            if correlation_id:
                payload["correlation_id"] = correlation_id
            timestamp = str(payload["timestamp"])
            connection.execute(
                """
                INSERT INTO runtime_events
                    (kind, job_id, sequence, timestamp, correlation_id, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    kind,
                    job_id,
                    payload["sequence"],
                    timestamp,
                    correlation_id,
                    self._encode(payload),
                ),
            )
            return payload

    def events(self, kind: str, job_id: str, *, after: int = 0) -> list[dict[str, Any]]:
        with self._connect() as connection:
            job = connection.execute(
                "SELECT 1 FROM runtime_jobs WHERE kind = ? AND job_id = ?",
                (kind, job_id),
            ).fetchone()
            if job is None:
                raise FileNotFoundError(f"Unknown {kind} job: {job_id}")
            rows = connection.execute(
                """
                SELECT payload_json
                FROM runtime_events
                WHERE kind = ? AND job_id = ? AND sequence > ?
                ORDER BY sequence ASC
                """,
                (kind, job_id, after),
            ).fetchall()
        return [self._decode(row["payload_json"]) for row in rows]

    def interrupt_active_jobs(
        self,
        kind: str,
        *,
        interrupted_at: str,
        message: str,
    ) -> list[dict[str, Any]]:
        recovered: list[dict[str, Any]] = []
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT job_id, correlation_id, payload_json
                FROM runtime_jobs
                WHERE kind = ? AND status IN (?, ?)
                ORDER BY created_at ASC
                """,
                (kind, *ACTIVE_JOB_STATUSES),
            ).fetchall()
            for row in rows:
                job = self._decode(row["payload_json"])
                error = {
                    "type": "RuntimeInterrupted",
                    "message": message,
                }
                job.update(
                    status="INTERRUPTED",
                    finished_at=interrupted_at,
                    error=error,
                )
                connection.execute(
                    """
                    UPDATE runtime_jobs
                    SET status = ?, updated_at = ?, payload_json = ?
                    WHERE kind = ? AND job_id = ?
                    """,
                    (
                        job["status"],
                        interrupted_at,
                        self._encode(job),
                        kind,
                        row["job_id"],
                    ),
                )
                sequence_row = connection.execute(
                    """
                    SELECT COALESCE(MAX(sequence), 0) AS sequence
                    FROM runtime_events
                    WHERE kind = ? AND job_id = ?
                    """,
                    (kind, row["job_id"]),
                ).fetchone()
                event = {
                    "type": "runtime.interrupted",
                    "timestamp": interrupted_at,
                    "sequence": int(sequence_row["sequence"]) + 1,
                    "error": error,
                }
                if row["correlation_id"]:
                    event["correlation_id"] = row["correlation_id"]
                connection.execute(
                    """
                    INSERT INTO runtime_events
                        (kind, job_id, sequence, timestamp, correlation_id, payload_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        kind,
                        row["job_id"],
                        event["sequence"],
                        interrupted_at,
                        row["correlation_id"],
                        self._encode(event),
                    ),
                )
                recovered.append(job)
        return recovered
