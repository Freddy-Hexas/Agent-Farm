from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class ProviderCircuitOpenError(RuntimeError):
    pass


class ProviderHealthStore:
    def __init__(self, path: Path, *, failure_threshold: int, cooldown_seconds: int) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS provider_health (
                    route TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    opened_until REAL,
                    rate_limited_until REAL,
                    last_error TEXT,
                    updated_at REAL NOT NULL
                )
                """
            )

    @staticmethod
    def _route(provider: str, model: str) -> str:
        return f"{provider}/{model}".casefold()

    def status(self, provider: str, model: str) -> dict[str, Any]:
        route = self._route(provider, model)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM provider_health WHERE route = ?", (route,)
            ).fetchone()
        now = time.time()
        if row is None:
            return {"status": "healthy", "route": route, "consecutive_failures": 0}
        opened_until = float(row["opened_until"] or 0)
        rate_limited_until = float(row["rate_limited_until"] or 0)
        status = "healthy"
        if rate_limited_until > now:
            status = "rate_limited"
        elif opened_until > now:
            status = "circuit_open"
        elif int(row["consecutive_failures"]) > 0:
            status = "degraded"
        return {
            "status": status,
            "route": route,
            "consecutive_failures": int(row["consecutive_failures"]),
            "success_count": int(row["success_count"]),
            "failure_count": int(row["failure_count"]),
            "opened_until": opened_until or None,
            "rate_limited_until": rate_limited_until or None,
            "last_error": row["last_error"],
        }

    def before_request(self, provider: str, model: str) -> dict[str, Any]:
        status = self.status(provider, model)
        if status["status"] == "rate_limited":
            raise ProviderCircuitOpenError(
                f"Provider route {status['route']} is rate limited until "
                f"{status['rate_limited_until']:.3f}."
            )
        if status["status"] == "circuit_open":
            raise ProviderCircuitOpenError(
                f"Provider circuit {status['route']} is open until {status['opened_until']:.3f}."
            )
        return status

    def record_success(self, provider: str, model: str) -> None:
        route = self._route(provider, model)
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO provider_health (
                    route, provider, model, consecutive_failures, success_count,
                    failure_count, opened_until, rate_limited_until, last_error, updated_at
                ) VALUES (?, ?, ?, 0, 1, 0, NULL, NULL, NULL, ?)
                ON CONFLICT(route) DO UPDATE SET
                    consecutive_failures = 0,
                    success_count = provider_health.success_count + 1,
                    opened_until = NULL,
                    rate_limited_until = NULL,
                    last_error = NULL,
                    updated_at = excluded.updated_at
                """,
                (route, provider, model, now),
            )

    def record_failure(self, provider: str, model: str, error: Exception) -> None:
        route = self._route(provider, model)
        now = time.time()
        status_code = getattr(error, "status_code", None)
        retry_after = getattr(error, "retry_after_seconds", None)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT consecutive_failures FROM provider_health WHERE route = ?", (route,)
            ).fetchone()
            failures = (int(row["consecutive_failures"]) if row else 0) + 1
            opened_until = now + self.cooldown_seconds if failures >= self.failure_threshold else None
            rate_limited_until = (
                now + max(1.0, float(retry_after or self.cooldown_seconds))
                if status_code == 429
                else None
            )
            connection.execute(
                """
                INSERT INTO provider_health (
                    route, provider, model, consecutive_failures, success_count,
                    failure_count, opened_until, rate_limited_until, last_error, updated_at
                ) VALUES (?, ?, ?, ?, 0, 1, ?, ?, ?, ?)
                ON CONFLICT(route) DO UPDATE SET
                    consecutive_failures = excluded.consecutive_failures,
                    failure_count = provider_health.failure_count + 1,
                    opened_until = excluded.opened_until,
                    rate_limited_until = COALESCE(excluded.rate_limited_until, provider_health.rate_limited_until),
                    last_error = excluded.last_error,
                    updated_at = excluded.updated_at
                """,
                (
                    route,
                    provider,
                    model,
                    failures,
                    opened_until,
                    rate_limited_until,
                    str(error)[:4_000],
                    now,
                ),
            )
