from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_farm.models import AgentFarmConfig
from agent_farm.usage_store import BudgetExceededError, BudgetManager, UsageLedger


def usage_event(request_id: str, cost: float | None) -> dict:
    return {
        "type": "model.request.completed",
        "request_id": request_id,
        "timestamp": "2026-08-02T10:00:00+00:00",
        "repository": "A:/repo",
        "farm_id": "farm-1",
        "agent_id": "worker-1",
        "agent_kind": "worker",
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "latency_ms": 125,
        "retry_count": 1,
        "usage": {
            "input_tokens": 100,
            "cached_input_tokens": 10,
            "output_tokens": 20,
            "total_tokens": 120,
            "estimated_cost_usd": cost,
        },
    }


class UsageLedgerTests(unittest.TestCase):
    def test_records_requests_idempotently_and_queries_scopes(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = UsageLedger(Path(tmp) / "usage.db")
            self.assertTrue(ledger.record(usage_event("request-1", 0.25)))
            self.assertFalse(ledger.record(usage_event("request-1", 0.25)))
            totals = ledger.total_cost(
                repository="A:/repo", farm_id="farm-1", agent_id="worker-1"
            )
            self.assertEqual(totals["request_count"], 1)
            self.assertEqual(totals["estimated_cost_usd"], 0.25)

    def test_hard_stop_enforces_worker_farm_monthly_and_unknown_price_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = UsageLedger(Path(tmp) / "usage.db")
            repository = Path(tmp) / "repo"
            event = usage_event("request-1", 0.25)
            event["repository"] = str(repository.resolve())
            ledger.record(event)
            manager = BudgetManager(
                ledger=ledger,
                config=AgentFarmConfig(
                    worker_budget_usd=0.25,
                    farm_budget_usd=1.0,
                    monthly_budget_usd=2.0,
                    budget_policy="hard-stop",
                ),
                repository=repository,
                context={
                    "farm_id": "farm-1",
                    "agent_id": "worker-1",
                    "agent_kind": "worker",
                },
            )
            with self.assertRaisesRegex(BudgetExceededError, "exhausted"):
                manager.before_request("deepseek", "deepseek-v4-flash")

            unknown_manager = BudgetManager(
                ledger=UsageLedger(Path(tmp) / "unknown.db"),
                config=AgentFarmConfig(worker_budget_usd=1.0, budget_policy="hard-stop"),
                repository=Path("A:/repo"),
                context={"agent_id": "worker-2", "agent_kind": "worker"},
            )
            with self.assertRaisesRegex(BudgetExceededError, "No trusted price"):
                unknown_manager.before_request("custom", "unknown-model")

    def test_warning_policy_warns_without_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = UsageLedger(Path(tmp) / "usage.db")
            repository = Path(tmp) / "repo"
            event = usage_event("request-1", 0.8)
            event["repository"] = str(repository.resolve())
            ledger.record(event)
            manager = BudgetManager(
                ledger=ledger,
                config=AgentFarmConfig(
                    worker_budget_usd=1.0,
                    budget_policy="warn",
                    budget_warning_ratio=0.8,
                ),
                repository=repository,
                context={
                    "farm_id": "farm-1",
                    "agent_id": "worker-1",
                    "agent_kind": "worker",
                },
            )
            assessment = manager.before_request("deepseek", "deepseek-v4-flash")
            self.assertEqual(assessment["status"], "warning")
            self.assertEqual(assessment["scopes"][0]["scope"], "worker")


if __name__ == "__main__":
    unittest.main()
