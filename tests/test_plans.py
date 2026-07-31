import unittest

from agent_farm.plans import SupervisorDecision, WorkerPlan


class WorkerPlanTests(unittest.TestCase):
    def test_parses_spec_contract_and_generates_worker_prompt(self):
        plan = WorkerPlan.from_dict(
            {
                "task_id": "Auth hardening",
                "max_parallel": 2,
                "workers": [
                    {
                        "id": "auth-impl",
                        "role": "implementation",
                        "profile": "cheap",
                        "goal": "Harden login rate limits.",
                        "allowed_paths": ["src/auth", "tests/auth"],
                        "test_commands": ["python -m unittest"],
                        "acceptance": ["Repeated failures are limited."],
                    }
                ],
            }
        )
        self.assertEqual(plan.task_id, "auth-hardening")
        self.assertEqual(plan.workers[0].profile, "cheap")
        prompt = plan.workers[0].to_task_spec()
        self.assertIn("expensive supervisor retains planning", prompt)
        self.assertIn("Harden login rate limits", prompt)

    def test_parses_collaborative_deliverable(self):
        plan = WorkerPlan.from_dict(
            {
                "task_id": "market-report",
                "deliverable": {
                    "path": "reports/market.md",
                    "instructions": "Combine both research briefs with linked sources.",
                },
                "workers": [
                    {
                        "id": "research",
                        "role": "Researcher",
                        "profile": "cheap",
                        "goal": "Research the market.",
                    }
                ],
            }
        )
        self.assertEqual(plan.deliverable.path, "reports/market.md")
        self.assertEqual(plan.to_json()["deliverable"]["path"], "reports/market.md")

    def test_rejects_deliverable_outside_repository(self):
        with self.assertRaisesRegex(ValueError, "inside the repository"):
            WorkerPlan.from_dict(
                {
                    "workers": [
                        {
                            "id": "research",
                            "role": "Researcher",
                            "profile": "cheap",
                            "goal": "Research.",
                        }
                    ],
                    "deliverable": {
                        "path": "../outside.md",
                        "instructions": "Write it.",
                    },
                }
            )

    def test_rejects_duplicate_worker_ids(self):
        worker = {
            "id": "same",
            "role": "implementation",
            "profile": "cheap",
            "goal": "Do one thing.",
        }
        with self.assertRaisesRegex(ValueError, "unique"):
            WorkerPlan.from_dict({"workers": [worker, worker]})

    def test_approve_merge_requires_selected_worker(self):
        with self.assertRaisesRegex(ValueError, "approved_worker"):
            SupervisorDecision.from_dict(
                {
                    "decision": "approve_merge",
                    "task_id": "farm-1",
                    "risk_level": "low",
                    "reason": "Looks good.",
                }
            )


if __name__ == "__main__":
    unittest.main()
