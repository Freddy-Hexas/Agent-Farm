from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_farm.usage_report import farm_usage_report


class UsageReportTests(unittest.TestCase):
    def test_aggregates_supervisor_and_worker_requests_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            farm_dir = root / ".agent-farm" / "farms" / "farm-1"
            worker_dir = root / ".agent-farm" / "runs" / "worker-1"
            farm_dir.mkdir(parents=True)
            worker_dir.mkdir(parents=True)
            worker_event = {
                "type": "model.request.completed",
                "request_id": "worker-request",
                "agent_kind": "worker",
                "agent_id": "one",
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "latency_ms": 100,
                "retry_count": 1,
                "usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 10,
                    "output_tokens": 20,
                    "total_tokens": 120,
                    "estimated_cost_usd": 0.00002,
                },
            }
            supervisor_event = {
                "type": "model.request.completed",
                "request_id": "supervisor-request",
                "agent_kind": "supervisor",
                "agent_id": "supervisor-review",
                "provider": "openai",
                "model": "unknown-premium",
                "latency_ms": 300,
                "retry_count": 0,
                "usage": {
                    "input_tokens": 200,
                    "cached_input_tokens": 0,
                    "output_tokens": 40,
                    "total_tokens": 240,
                    "estimated_cost_usd": None,
                },
            }
            (worker_dir / "worker-events.jsonl").write_text(json.dumps(worker_event) + "\n")
            (farm_dir / "supervisor-review-events.jsonl").write_text(
                json.dumps(supervisor_event) + "\n"
            )
            result = {
                "farm_id": "farm-1",
                "workers": [{"id": "one", "run_dir": str(worker_dir)}],
            }

            report = farm_usage_report(root, farm_dir, result)
            self.assertEqual(report["workers"]["request_count"], 1)
            self.assertEqual(report["workers"]["estimated_cost_usd"], 0.00002)
            self.assertEqual(report["workers"]["retry_count"], 1)
            self.assertEqual(report["supervisor"]["total_tokens"], 240)
            self.assertEqual(report["supervisor"]["unpriced_requests"], 1)
            self.assertEqual(report["total"]["request_count"], 2)
            self.assertEqual(len(report["requests"]), 2)

    def test_reports_cost_per_accepted_artifact_and_premium_route_savings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            farm_dir = root / ".agent-farm" / "farms" / "farm-1"
            worker_dir = root / ".agent-farm" / "runs" / "worker-1"
            farm_dir.mkdir(parents=True)
            worker_dir.mkdir(parents=True)
            common_usage = {
                "input_tokens": 100_000,
                "cached_input_tokens": 0,
                "cache_write_input_tokens": 0,
                "output_tokens": 20_000,
                "total_tokens": 120_000,
            }
            worker_event = {
                "type": "model.request.completed",
                "request_id": "worker",
                "agent_kind": "worker",
                "agent_id": "one",
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "usage": {**common_usage, "estimated_cost_usd": 0.0196},
            }
            supervisor_event = {
                "type": "model.request.completed",
                "request_id": "supervisor",
                "agent_kind": "supervisor",
                "agent_id": "supervisor-review",
                "provider": "openai",
                "model": "gpt-5.4",
                "usage": {**common_usage, "estimated_cost_usd": 0.55},
            }
            (worker_dir / "worker-events.jsonl").write_text(json.dumps(worker_event) + "\n")
            (farm_dir / "supervisor-review-events.jsonl").write_text(
                json.dumps(supervisor_event) + "\n"
            )
            report = farm_usage_report(
                root,
                farm_dir,
                {
                    "farm_id": "farm-1",
                    "status": "SUPERVISOR_APPROVED",
                    "workers": [{"id": "one", "run_dir": str(worker_dir)}],
                },
            )
            economics = report["economics"]
            self.assertEqual(economics["accepted_artifact_count"], 1)
            self.assertEqual(economics["cost_per_accepted_artifact_usd"], 0.5696)
            self.assertEqual(economics["premium_only_estimate_usd"], 1.1)
            self.assertEqual(economics["estimated_savings_usd"], 0.5304)
            self.assertGreater(economics["estimated_savings_percent"], 48)


if __name__ == "__main__":
    unittest.main()
