from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .session_ledger import project_job_from_events, project_session_events


ACTIVE_JOB_STATUSES = ("QUEUED", "RUNNING")
RUNTIME_SCHEMA_VERSION = 3


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

                CREATE TABLE IF NOT EXISTS runtime_sessions (
                    session_id TEXT PRIMARY KEY,
                    parent_session_id TEXT,
                    farm_id TEXT,
                    thread_id TEXT,
                    turn_id TEXT,
                    role TEXT NOT NULL,
                    harness_id TEXT,
                    provider_id TEXT,
                    model_id TEXT,
                    route_id TEXT,
                    status TEXT NOT NULL,
                    stop_reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS runtime_sessions_parent
                    ON runtime_sessions (parent_session_id, created_at ASC);
                CREATE INDEX IF NOT EXISTS runtime_sessions_farm
                    ON runtime_sessions (farm_id, created_at ASC);

                CREATE TABLE IF NOT EXISTS session_events (
                    session_id TEXT NOT NULL,
                    event_seq INTEGER NOT NULL,
                    event_id TEXT NOT NULL UNIQUE,
                    schema_version INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    parent_session_id TEXT,
                    farm_id TEXT,
                    thread_id TEXT,
                    turn_id TEXT,
                    correlation_id TEXT,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (session_id, event_seq),
                    FOREIGN KEY (session_id)
                        REFERENCES runtime_sessions (session_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS session_events_correlation
                    ON session_events (correlation_id, event_seq ASC);
                CREATE INDEX IF NOT EXISTS session_events_farm
                    ON session_events (farm_id, session_id, event_seq ASC);
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
            if version < RUNTIME_SCHEMA_VERSION:
                self._migrate_legacy_runtime_events(connection)
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

    def _migrate_legacy_runtime_events(self, connection: sqlite3.Connection) -> None:
        """Backfill v1/v2 job events into the session ledger exactly once."""
        # A legacy database can contain thousands of events. Read duplicate
        # markers once instead of issuing a JSON query for every event.
        migrated_markers: set[tuple[str, str, str, int]] = set()
        for existing in connection.execute(
            "SELECT session_id, payload_json FROM session_events"
        ).fetchall():
            payload = self._decode(existing["payload_json"])
            if {
                "legacy_kind",
                "legacy_job_id",
                "legacy_sequence",
            }.issubset(payload):
                migrated_markers.add(
                    (
                        str(existing["session_id"]),
                        str(payload["legacy_kind"]),
                        str(payload["legacy_job_id"]),
                        int(payload["legacy_sequence"]),
                    )
                )
        jobs = connection.execute(
            "SELECT kind, job_id, correlation_id, payload_json FROM runtime_jobs"
        ).fetchall()
        for row in jobs:
            job = self._decode(row["payload_json"])
            session_id = str(job.get("session_id") or row["job_id"])
            created_at = str(job.get("created_at") or self._now())
            session = {
                "session_id": session_id,
                "role": job.get("role") or ("supervisor" if row["kind"] == "plan" else "farm"),
                "status": str(job.get("status") or "created").lower(),
                "parent_session_id": job.get("parent_session_id"),
                "farm_id": job.get("farm_id"),
                "thread_id": job.get("thread_id"),
                "turn_id": job.get("turn_id"),
                "correlation_id": row["correlation_id"],
                "created_at": created_at,
            }
            self._ensure_session_connection(connection, session_id, session=session)
            events = connection.execute(
                """
                SELECT sequence, timestamp, correlation_id, payload_json
                FROM runtime_events
                WHERE kind = ? AND job_id = ? ORDER BY sequence ASC
                """,
                (row["kind"], row["job_id"]),
            ).fetchall()
            for event_row in events:
                event = self._decode(event_row["payload_json"])
                marker = {
                    "legacy_kind": row["kind"],
                    "legacy_job_id": row["job_id"],
                    "legacy_sequence": int(event_row["sequence"]),
                }
                marker_key = (
                    session_id,
                    str(row["kind"]),
                    str(row["job_id"]),
                    int(event_row["sequence"]),
                )
                if marker_key in migrated_markers:
                    continue
                event.update(marker)
                event.setdefault("timestamp", event_row["timestamp"])
                event.setdefault("correlation_id", event_row["correlation_id"] or row["correlation_id"])
                event["session_id"] = session_id
                self._append_session_event_connection(connection, session_id, event, session=session)
                migrated_markers.add(marker_key)

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
            connection.execute("BEGIN IMMEDIATE")
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
            session_id = job.get("session_id")
            if session_id:
                self._ensure_session_connection(
                    connection,
                    self._session_id(session_id),
                    session={
                        "session_id": session_id,
                        "role": job.get("role") or ("supervisor" if kind == "plan" else "farm"),
                        "status": str(status).lower(),
                        "farm_id": job.get("farm_id"),
                        "thread_id": job.get("thread_id"),
                        "turn_id": job.get("turn_id"),
                        "correlation_id": job.get("correlation_id"),
                        "harness_id": job.get("harness_id"),
                        "provider_id": job.get("provider_id"),
                        "model_id": job.get("model_id"),
                        "route_id": job.get("route_id"),
                        "created_at": created_at,
                    },
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

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _session_id(value: Any) -> str:
        session_id = str(value or "").strip()
        if not session_id or len(session_id) > 160:
            raise ValueError("session_id must be a non-empty string of at most 160 characters.")
        return session_id

    def create_session(self, session: dict[str, Any]) -> dict[str, Any]:
        """Create or refresh a durable session descriptor.

        The operation is idempotent so a late stream event can safely create an
        implicit runtime session after a daemon restart.
        """

        session_id = self._session_id(session.get("session_id"))
        now = str(session.get("created_at") or self._now())
        payload = dict(session)
        payload.update(
            {
                "schema_version": int(payload.get("schema_version", 1)),
                "session_id": session_id,
                "role": str(payload.get("role") or payload.get("agent_kind") or "runtime"),
                "status": str(payload.get("status") or "created").lower(),
                "created_at": now,
                "updated_at": str(payload.get("updated_at") or now),
            }
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT payload_json FROM runtime_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO runtime_sessions
                        (session_id, parent_session_id, farm_id, thread_id, turn_id,
                         role, harness_id, provider_id, model_id, route_id, status,
                         stop_reason, created_at, updated_at, payload_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        payload.get("parent_session_id"),
                        payload.get("farm_id"),
                        payload.get("thread_id"),
                        payload.get("turn_id"),
                        payload["role"],
                        payload.get("harness_id"),
                        payload.get("provider_id") or payload.get("provider"),
                        payload.get("model_id") or payload.get("model"),
                        payload.get("route_id"),
                        payload["status"],
                        payload.get("stop_reason"),
                        payload["created_at"],
                        payload["updated_at"],
                        self._encode(payload),
                    ),
                )
            else:
                current = self._decode(existing["payload_json"])
                current.update({key: value for key, value in payload.items() if key != "session_id"})
                current["session_id"] = session_id
                current["updated_at"] = payload["updated_at"]
                connection.execute(
                    """
                    UPDATE runtime_sessions
                    SET parent_session_id = COALESCE(?, parent_session_id),
                        farm_id = COALESCE(?, farm_id),
                        thread_id = COALESCE(?, thread_id),
                        turn_id = COALESCE(?, turn_id),
                        role = ?, harness_id = COALESCE(?, harness_id),
                        provider_id = COALESCE(?, provider_id),
                        model_id = COALESCE(?, model_id), route_id = COALESCE(?, route_id),
                        status = ?, stop_reason = ?,
                        updated_at = ?, payload_json = ?
                    WHERE session_id = ?
                    """,
                    (
                        current.get("parent_session_id"),
                        current.get("farm_id"),
                        current.get("thread_id"),
                        current.get("turn_id"),
                        current.get("role") or "runtime",
                        current.get("harness_id"),
                        current.get("provider_id") or current.get("provider"),
                        current.get("model_id") or current.get("model"),
                        current.get("route_id"),
                        current.get("status") or "created",
                        current.get("stop_reason"),
                        current.get("updated_at") or now,
                        self._encode(current),
                        session_id,
                    ),
                )
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> dict[str, Any]:
        session_id = self._session_id(session_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM runtime_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            raise FileNotFoundError(f"Unknown session: {session_id}")
        return self._decode(row["payload_json"])

    def list_sessions(
        self,
        *,
        parent_session_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000.")
        with self._connect() as connection:
            if parent_session_id is None:
                rows = connection.execute(
                    """
                    SELECT payload_json FROM runtime_sessions
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            else:
                parent_session_id = self._session_id(parent_session_id)
                rows = connection.execute(
                    """
                    SELECT payload_json FROM runtime_sessions
                    WHERE parent_session_id = ?
                    ORDER BY created_at ASC LIMIT ?
                    """,
                    (parent_session_id, limit),
                ).fetchall()
        return [self._decode(row["payload_json"]) for row in rows]

    def update_session(self, session_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        current = self.get_session(session_id)
        current.update(changes)
        current["session_id"] = session_id
        current["updated_at"] = str(changes.get("updated_at") or self._now())
        return self.create_session(current)

    def _ensure_session_connection(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        *,
        event: dict[str, Any] | None = None,
        session: dict[str, Any] | None = None,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM runtime_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is not None:
            return row
        source = dict(session or {})
        source.update(event or {})
        source["session_id"] = session_id
        source.setdefault("role", source.get("agent_kind") or "runtime")
        source.setdefault("status", "running")
        source.setdefault("created_at", source.get("timestamp") or source.get("created_at") or self._now())
        source.setdefault("updated_at", source["created_at"])
        connection.execute(
            """
            INSERT INTO runtime_sessions
                (session_id, parent_session_id, farm_id, thread_id, turn_id,
                 role, harness_id, provider_id, model_id, route_id, status,
                 stop_reason, created_at, updated_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                source.get("parent_session_id"),
                source.get("farm_id"),
                source.get("thread_id"),
                source.get("turn_id"),
                str(source.get("role") or "runtime"),
                source.get("harness_id"),
                source.get("provider_id") or source.get("provider"),
                source.get("model_id") or source.get("model"),
                source.get("route_id"),
                str(source.get("status") or "running").lower(),
                source.get("stop_reason"),
                str(source["created_at"]),
                str(source["updated_at"]),
                self._encode(source),
            ),
        )
        return connection.execute(
            "SELECT * FROM runtime_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()

    def _append_session_event_connection(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        event: dict[str, Any],
        *,
        session: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session_id = self._session_id(session_id)
        current = self._ensure_session_connection(connection, session_id, event=event, session=session)
        row = connection.execute(
            """
            SELECT COALESCE(MAX(event_seq), 0) AS event_seq
            FROM session_events WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        event_seq = int(row["event_seq"]) + 1
        payload = dict(event)
        payload.pop("sequence", None)
        payload.pop("event_seq", None)
        payload.setdefault("schema_version", 1)
        payload["event_id"] = str(payload.get("event_id") or f"event-{uuid.uuid4().hex}")
        payload["event_seq"] = event_seq
        payload["sequence"] = event_seq
        payload["session_id"] = session_id
        payload["event_type"] = str(payload.get("event_type") or payload.get("type") or "event")
        payload["type"] = payload["event_type"]
        payload["created_at"] = str(
            payload.get("created_at") or payload.get("timestamp") or self._now()
        )
        payload.setdefault("timestamp", payload["created_at"])
        parent_session_id = payload.get("parent_session_id") or current["parent_session_id"]
        if parent_session_id:
            payload["parent_session_id"] = parent_session_id
        correlation_id = payload.get("correlation_id")
        connection.execute(
            """
            INSERT INTO session_events
                (session_id, event_seq, event_id, schema_version, event_type,
                 parent_session_id, farm_id, thread_id, turn_id, correlation_id,
                 created_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                event_seq,
                payload["event_id"],
                int(payload.get("schema_version", 1)),
                payload["event_type"],
                parent_session_id,
                payload.get("farm_id") or current["farm_id"],
                payload.get("thread_id") or current["thread_id"],
                payload.get("turn_id") or current["turn_id"],
                correlation_id,
                payload["created_at"],
                self._encode(payload),
            ),
        )
        status = self._session_status_from_event(payload)
        stop_reason = self._stop_reason_from_status(status)
        event_type = str(payload.get("event_type") or payload.get("type") or "")
        if current["role"] in {"farm", "supervisor", "plan", "thread"} and not (
            event_type.startswith("session/")
            or event_type.startswith("job.")
            or event_type.startswith("farm.")
            or event_type.startswith("task.")
        ):
            status = None
            stop_reason = None
        updated_at = payload["created_at"]
        current_payload = self._decode(current["payload_json"])
        current_payload["updated_at"] = updated_at
        for key in (
            "parent_session_id",
            "farm_id",
            "thread_id",
            "turn_id",
            "harness_id",
            "provider_id",
            "model_id",
            "route_id",
            "correlation_id",
        ):
            if payload.get(key) is not None:
                current_payload[key] = payload[key]
        if status:
            current_payload["status"] = status
        if stop_reason:
            current_payload["stop_reason"] = stop_reason
        connection.execute(
            """
            UPDATE runtime_sessions
            SET parent_session_id = COALESCE(?, parent_session_id),
                farm_id = COALESCE(?, farm_id),
                thread_id = COALESCE(?, thread_id),
                turn_id = COALESCE(?, turn_id),
                harness_id = COALESCE(?, harness_id),
                provider_id = COALESCE(?, provider_id),
                model_id = COALESCE(?, model_id),
                route_id = COALESCE(?, route_id),
                status = COALESCE(?, status),
                stop_reason = COALESCE(?, stop_reason),
                updated_at = ?, payload_json = ?
            WHERE session_id = ?
            """,
            (
                current_payload.get("parent_session_id"),
                current_payload.get("farm_id"),
                current_payload.get("thread_id"),
                current_payload.get("turn_id"),
                current_payload.get("harness_id"),
                current_payload.get("provider_id"),
                current_payload.get("model_id"),
                current_payload.get("route_id"),
                status,
                stop_reason,
                updated_at,
                self._encode(current_payload),
                session_id,
            ),
        )
        return payload

    @staticmethod
    def _session_status_from_event(event: dict[str, Any]) -> str | None:
        event_type = str(event.get("event_type") or event.get("type") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        candidate = payload.get("status") or event.get("status")
        if isinstance(candidate, str) and candidate.lower() in {
            "created", "queued", "running", "cancelling", "completed",
            "cancelled", "interrupted", "failed", "blocked",
        }:
            return candidate.lower()
        suffix = event_type.rsplit("/", 1)[-1].lower()
        if suffix in {"started", "resumed"}:
            return "running"
        if suffix in {"spawned", "forked"}:
            return "queued"
        if suffix in {"completed", "cancelled", "interrupted", "failed", "blocked"}:
            return suffix
        if suffix in {"cancel_requested", "interrupt_requested"}:
            return "cancelling"
        return None

    @staticmethod
    def _stop_reason_from_status(status: str | None) -> str | None:
        return {
            "completed": "completed",
            "cancelled": "cancelled",
            "interrupted": "cancelled",
            "failed": "failed",
            "blocked": "blocked",
        }.get(status or "")

    def append_session_event(self, session_id: str, event: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self._append_session_event_connection(connection, session_id, event)

    def session_events(
        self,
        session_id: str,
        *,
        after: int = 0,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        session_id = self._session_id(session_id)
        if type(after) is not int or after < 0:
            raise ValueError("after must be a non-negative integer.")
        if limit < 1 or limit > 5000:
            raise ValueError("limit must be between 1 and 5000.")
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM runtime_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if exists is None:
                raise FileNotFoundError(f"Unknown session: {session_id}")
            rows = connection.execute(
                """
                SELECT payload_json FROM session_events
                WHERE session_id = ? AND event_seq > ?
                ORDER BY event_seq ASC LIMIT ?
                """,
                (session_id, after, limit),
            ).fetchall()
        return [self._decode(row["payload_json"]) for row in rows]

    def session_children(self, session_id: str) -> list[dict[str, Any]]:
        return self.list_sessions(parent_session_id=session_id)

    def session_projection(self, session_id: str) -> dict[str, Any]:
        session = self.get_session(session_id)
        return project_session_events(self.session_events(session_id, limit=5000), session=session)

    def rebuild_session(self, session_id: str) -> dict[str, Any]:
        """Explicit replay entry point used by recovery and diagnostics."""
        return self.session_projection(session_id)

    def project_job(self, kind: str, job_id: str) -> dict[str, Any]:
        job = self.get_job(kind, job_id)
        session_id = str(job.get("session_id") or job_id)
        try:
            events = self.session_events(session_id, limit=5000)
            for child in self.session_children(session_id):
                try:
                    events.extend(self.session_events(child["session_id"], limit=5000))
                except FileNotFoundError:
                    continue
        except FileNotFoundError:
            events = self.events(kind, job_id)
        return project_job_from_events(job, events)

    def rebuild_job_projection(self, kind: str, job_id: str) -> dict[str, Any]:
        return self.project_job(kind, job_id)

    def append_event(
        self,
        kind: str,
        job_id: str,
        event: dict[str, Any],
    ) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            job = connection.execute(
                "SELECT correlation_id, payload_json FROM runtime_jobs WHERE kind = ? AND job_id = ?",
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
            raw_job = self._decode(job["payload_json"])
            session_id = payload.get("session_id") or raw_job.get("session_id") or job_id
            ledger_payload = dict(payload)
            # `sequence` belongs to the compatibility job stream.  The session
            # ledger assigns its own contiguous cursor.
            ledger_payload.pop("sequence", None)
            ledger_payload.pop("event_seq", None)
            if raw_job.get("parent_session_id") and not ledger_payload.get("parent_session_id"):
                ledger_payload["parent_session_id"] = raw_job["parent_session_id"]
            ledger_session = {
                "session_id": session_id,
                "role": ledger_payload.get("agent_kind") or raw_job.get("role") or ("supervisor" if kind == "plan" else "farm"),
                "status": str(raw_job.get("status") or "running").lower(),
                "parent_session_id": ledger_payload.get("parent_session_id") or raw_job.get("parent_session_id"),
                "farm_id": ledger_payload.get("farm_id") or raw_job.get("farm_id"),
                "thread_id": ledger_payload.get("thread_id") or raw_job.get("thread_id"),
                "turn_id": ledger_payload.get("turn_id") or raw_job.get("turn_id"),
                "harness_id": ledger_payload.get("harness_id") or raw_job.get("harness_id"),
                "provider_id": ledger_payload.get("provider_id") or raw_job.get("provider_id"),
                "model_id": ledger_payload.get("model_id") or raw_job.get("model_id"),
                "route_id": ledger_payload.get("route_id") or raw_job.get("route_id"),
                "correlation_id": ledger_payload.get("correlation_id") or raw_job.get("correlation_id"),
                "created_at": raw_job.get("created_at") or payload["timestamp"],
            }
            self._append_session_event_connection(
                connection,
                self._session_id(session_id),
                ledger_payload,
                session=ledger_session,
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
                raw_session_id = job.get("session_id") or row["job_id"]
                ledger_event = {
                    "type": "session/interrupted",
                    "timestamp": interrupted_at,
                    "error": error,
                    "status": "interrupted",
                    "correlation_id": row["correlation_id"],
                    "parent_session_id": job.get("parent_session_id"),
                    "farm_id": job.get("farm_id"),
                    "thread_id": job.get("thread_id"),
                    "turn_id": job.get("turn_id"),
                }
                self._append_session_event_connection(
                    connection,
                    self._session_id(raw_session_id),
                    ledger_event,
                    session={
                        "session_id": raw_session_id,
                        "role": job.get("role") or ("supervisor" if kind == "plan" else "farm"),
                        "parent_session_id": job.get("parent_session_id"),
                        "farm_id": job.get("farm_id"),
                        "thread_id": job.get("thread_id"),
                        "turn_id": job.get("turn_id"),
                        "created_at": job.get("created_at") or interrupted_at,
                        "status": "interrupted",
                    },
                )
                recovered.append(job)
        return recovered
