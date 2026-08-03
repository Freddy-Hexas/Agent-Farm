from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .models import AgentFarmConfig
from .usage import price_for_model


class BudgetExceededError(RuntimeError):
    pass


def default_usage_database() -> Path:
    override = os.environ.get("AGENT_FARM_USAGE_DB")
    if override:
        return Path(override).expanduser().resolve()
    local = os.environ.get("LOCALAPPDATA")
    root = Path(local) if local else Path.home() / ".agent-farm"
    return (root / "AgentFarm" / "usage.db").resolve()


class UsageLedger:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
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
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS model_usage (
                    request_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    month TEXT NOT NULL,
                    repository TEXT NOT NULL,
                    farm_id TEXT,
                    agent_id TEXT,
                    agent_kind TEXT NOT NULL,
                    profile TEXT,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    cached_input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    total_tokens INTEGER NOT NULL,
                    retry_count INTEGER NOT NULL,
                    latency_ms REAL NOT NULL,
                    estimated_cost_usd REAL,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS model_usage_month
                    ON model_usage (month, created_at);
                CREATE INDEX IF NOT EXISTS model_usage_farm
                    ON model_usage (repository, farm_id, created_at);
                CREATE INDEX IF NOT EXISTS model_usage_worker
                    ON model_usage (repository, farm_id, agent_id, created_at);
                """
            )

    def record(self, event: dict[str, Any]) -> bool:
        request_id = event.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("Usage event requires a request_id.")
        usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
        created_at = str(event.get("timestamp") or datetime.now(timezone.utc).isoformat())
        month = created_at[:7]
        values = (
            request_id,
            created_at,
            month,
            str(event.get("repository") or ""),
            str(event.get("farm_id")) if event.get("farm_id") else None,
            str(event.get("agent_id")) if event.get("agent_id") else None,
            str(event.get("agent_kind") or "worker"),
            str(event.get("profile")) if event.get("profile") else None,
            str(event.get("provider") or "unknown"),
            str(event.get("model") or "unknown"),
            int(usage.get("input_tokens") or 0),
            int(usage.get("cached_input_tokens") or 0),
            int(usage.get("output_tokens") or 0),
            int(usage.get("total_tokens") or 0),
            int(event.get("retry_count") or 0),
            float(event.get("latency_ms") or 0),
            (
                float(usage["estimated_cost_usd"])
                if isinstance(usage.get("estimated_cost_usd"), (int, float))
                and not isinstance(usage.get("estimated_cost_usd"), bool)
                else None
            ),
            json.dumps(event, ensure_ascii=True, separators=(",", ":")),
        )
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO model_usage (
                    request_id, created_at, month, repository, farm_id, agent_id,
                    agent_kind, profile, provider, model, input_tokens,
                    cached_input_tokens, output_tokens, total_tokens, retry_count,
                    latency_ms, estimated_cost_usd, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            return cursor.rowcount == 1

    def total_cost(
        self,
        *,
        month: str | None = None,
        repository: str | None = None,
        farm_id: str | None = None,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        clauses: list[str] = []
        parameters: list[str] = []
        for column, value in (
            ("month", month),
            ("repository", repository),
            ("farm_id", farm_id),
            ("agent_id", agent_id),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                parameters.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT COALESCE(SUM(estimated_cost_usd), 0) AS cost,
                       COUNT(*) AS requests,
                       SUM(CASE WHEN estimated_cost_usd IS NULL THEN 1 ELSE 0 END) AS unpriced
                FROM model_usage{where}
                """,  # nosec B608: columns are fixed above; values remain parameters.
                parameters,
            ).fetchone()
        return {
            "estimated_cost_usd": round(float(row["cost"]), 9),
            "request_count": int(row["requests"]),
            "unpriced_requests": int(row["unpriced"] or 0),
        }


class BudgetManager:
    def __init__(
        self,
        *,
        ledger: UsageLedger,
        config: AgentFarmConfig,
        repository: Path,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.ledger = ledger
        self.config = config
        self.repository = str(repository.resolve())
        self.context = dict(context or {})

    def assessment(self, provider: str, model: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        month = now.strftime("%Y-%m")
        farm_id = str(self.context.get("farm_id") or "") or None
        agent_id = str(self.context.get("agent_id") or "") or None
        kind = str(self.context.get("agent_kind") or "worker")
        scopes: list[dict[str, Any]] = []
        if kind == "worker" and self.config.worker_budget_usd is not None:
            totals = self.ledger.total_cost(
                repository=self.repository, farm_id=farm_id, agent_id=agent_id
            )
            scopes.append({"scope": "worker", "limit_usd": self.config.worker_budget_usd, **totals})
        if farm_id and self.config.farm_budget_usd is not None:
            totals = self.ledger.total_cost(repository=self.repository, farm_id=farm_id)
            scopes.append({"scope": "farm", "limit_usd": self.config.farm_budget_usd, **totals})
        if self.config.monthly_budget_usd is not None:
            totals = self.ledger.total_cost(month=month)
            scopes.append({"scope": "monthly", "limit_usd": self.config.monthly_budget_usd, **totals})

        _, price = price_for_model(provider, model, self.config.model_price_overrides)
        unpriced_model = price is None
        over = [item for item in scopes if item["estimated_cost_usd"] >= item["limit_usd"]]
        warning = [
            item
            for item in scopes
            if item["estimated_cost_usd"] >= item["limit_usd"] * self.config.budget_warning_ratio
        ]
        status = "ok"
        reason = None
        if self.config.budget_policy == "hard-stop" and scopes and unpriced_model:
            status = "denied"
            reason = f"No trusted price is available for {provider}/{model}."
        elif over and self.config.budget_policy == "hard-stop":
            status = "denied"
            reason = "One or more configured cost budgets have been exhausted."
        elif over or warning or (scopes and unpriced_model):
            status = "warning"
            reason = "A cost budget needs attention."
        return {
            "status": status,
            "policy": self.config.budget_policy,
            "reason": reason,
            "provider": provider,
            "model": model,
            "unpriced_model": unpriced_model,
            "scopes": scopes,
        }

    def before_request(self, provider: str, model: str) -> dict[str, Any]:
        assessment = self.assessment(provider, model)
        if assessment["status"] == "denied":
            raise BudgetExceededError(str(assessment["reason"]))
        return assessment

    def record(self, event: dict[str, Any]) -> dict[str, Any]:
        enriched = {"repository": self.repository, **self.context, **event}
        self.ledger.record(enriched)
        return self.assessment(str(event.get("provider") or ""), str(event.get("model") or ""))
