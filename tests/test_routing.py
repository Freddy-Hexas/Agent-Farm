from __future__ import annotations

import unittest

from agent_farm.models import AgentFarmConfig
from agent_farm.plans import WorkerPlan, WorkerPlanItem
from agent_farm.routing import RoutingError, escalation_profiles, route_worker_plan


def worker(profile: str, complexity: str) -> WorkerPlanItem:
    return WorkerPlanItem(
        worker_id="one",
        role="implementation",
        profile=profile,
        goal="Implement the requested change.",
        complexity=complexity,
    )


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.config = AgentFarmConfig(
            model_price_overrides={
                "gateway/economy": {"input": 0.1, "output": 0.2},
                "gateway/standard": {"input": 1.0, "output": 2.0},
                "gateway/premium": {"input": 5.0, "output": 10.0},
            },
            worker_profiles={
                "economy": {
                    "model": "economy",
                    "provider": "gateway",
                    "capability_tier": "economy",
                },
                "standard": {
                    "model": "standard",
                    "provider": "gateway",
                    "capability_tier": "standard",
                },
                "premium": {
                    "model": "premium",
                    "provider": "gateway",
                    "capability_tier": "premium",
                },
            },
        )

    def test_simple_task_is_routed_to_least_expensive_capable_profile(self):
        plan = WorkerPlan(task_id="task", workers=[worker("premium", "simple")])
        routed, decisions = route_worker_plan(self.config, plan)
        self.assertEqual(routed.workers[0].profile, "economy")
        self.assertEqual(decisions[0]["reason"], "least_expensive_capable_priced_route")
        self.assertLess(decisions[0]["reference_cost_usd"], 0.1)

    def test_standard_and_complex_tasks_keep_capability_floor(self):
        standard, _ = route_worker_plan(
            self.config,
            WorkerPlan(task_id="task", workers=[worker("premium", "standard")]),
        )
        complex_plan, _ = route_worker_plan(
            self.config,
            WorkerPlan(task_id="task", workers=[worker("economy", "complex")]),
        )
        self.assertEqual(standard.workers[0].profile, "standard")
        self.assertEqual(complex_plan.workers[0].profile, "premium")

    def test_unknown_price_does_not_beat_a_known_price(self):
        config = AgentFarmConfig(
            model_price_overrides={"known/model": {"input": 1, "output": 2}},
            worker_profiles={
                "unknown": {
                    "model": "mystery",
                    "provider": "custom",
                    "capability_tier": "economy",
                },
                "known": {
                    "model": "model",
                    "provider": "known",
                    "capability_tier": "economy",
                },
            },
        )
        routed, _ = route_worker_plan(
            config, WorkerPlan(task_id="task", workers=[worker("unknown", "simple")])
        )
        self.assertEqual(routed.workers[0].profile, "known")

    def test_complex_task_fails_when_no_capable_route_exists(self):
        config = AgentFarmConfig(
            worker_profiles={
                "economy": {
                    "model": "local",
                    "provider": "ollama",
                    "capability_tier": "economy",
                }
            }
        )
        with self.assertRaisesRegex(RoutingError, "no configured route"):
            route_worker_plan(
                config, WorkerPlan(task_id="task", workers=[worker("economy", "complex")])
            )

    def test_explicit_fallback_precedes_automatic_escalation(self):
        config = AgentFarmConfig(
            model_price_overrides=self.config.model_price_overrides,
            worker_profiles={
                **self.config.worker_profiles,
                "economy": {
                    **self.config.worker_profiles["economy"],
                    "fallback_profiles": ["premium"],
                },
            },
        )
        self.assertEqual(escalation_profiles(config, "economy")[:2], ["premium", "standard"])


if __name__ == "__main__":
    unittest.main()
