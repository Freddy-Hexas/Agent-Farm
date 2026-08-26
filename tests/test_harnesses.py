import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_farm.harnesses import (
    HarnessCapabilityError,
    HarnessDescriptor,
    HarnessRegistry,
    available_harnesses,
    effective_harness,
    event_metadata,
    route_id,
)
from agent_farm.models import AgentFarmConfig, CommandResult, RunPaths
from agent_farm.orchestrator import run_codex_worker


class HarnessTests(unittest.TestCase):
    def test_role_harnesses_override_legacy_backend(self):
        config = AgentFarmConfig(agent_backend="codex", worker_harness="native")

        self.assertEqual(effective_harness(config, "supervisor"), "codex")
        self.assertEqual(effective_harness(config, "worker"), "native")
        self.assertEqual(route_id("deepseek", "deepseek-v4"), "deepseek/deepseek-v4")

    def test_registry_publishes_native_and_codex_descriptors(self):
        descriptors = available_harnesses(AgentFarmConfig(codex_binary="missing-codex"))
        by_id = {item["harness_id"]: item for item in descriptors}

        self.assertEqual(set(by_id), {"native", "codex"})
        self.assertTrue(by_id["native"]["ready"])
        self.assertFalse(by_id["codex"]["ready"])
        self.assertIn("streaming", by_id["native"]["capabilities"])

    def test_registry_rejects_missing_capability_before_runner(self):
        calls = []
        registry = HarnessRegistry(
            descriptors={
                "native": HarnessDescriptor(
                    harness_id="native",
                    display_name="Native",
                    capabilities=("streaming",),
                    transports=("in_process",),
                    supports={},
                )
            },
            runners={"native": lambda **kwargs: calls.append(kwargs)},
        )

        with self.assertRaises(HarnessCapabilityError):
            registry.require("native", required_capabilities={"approval_requests"})
        self.assertEqual(calls, [])

    def test_codex_events_use_native_event_shape_and_stable_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            paths = RunPaths(repo_root=root, run_dir=run_dir, worktree=root / "worktree")
            run_dir.mkdir()
            paths.worktree.mkdir()
            config = AgentFarmConfig(
                agent_backend="codex",
                codex_binary=sys.executable,
                worker_provider="deepseek",
                worker_model="deepseek-chat",
            )

            def fake_codex(**kwargs):
                kwargs["events_file"].write_text(
                    json.dumps(
                        {
                            "type": "model.request.completed",
                            "request_id": "request-1",
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                return CommandResult(args=["codex"], cwd=str(root), returncode=0)

            events = []
            with patch("agent_farm.orchestrator.run_legacy_codex_worker", side_effect=fake_codex):
                result = run_codex_worker(
                    config=config,
                    paths=paths,
                    prompt="run",
                    model=None,
                    timeout_seconds=None,
                    event_callback=events.append,
                )

            self.assertTrue(result.ok)
            recorded = [json.loads(line) for line in paths.worker_events_file.read_text().splitlines()]
            self.assertEqual([item["type"] for item in recorded], [
                "agent.started",
                "model.request.completed",
                "agent.completed",
            ])
            for item in recorded:
                self.assertEqual(item["harness_id"], "codex")
                self.assertEqual(item["route_id"], "deepseek/deepseek-chat")
                self.assertEqual(item["session_id"], "run")
            self.assertEqual(len(events), len(recorded))
            self.assertTrue(paths.worker_raw_events_file.is_file())


if __name__ == "__main__":
    unittest.main()
