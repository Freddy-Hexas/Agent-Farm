import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_farm.models import CommandResult
from agent_farm.native_agent import NativeAgentResult
from agent_farm.supervisor import (
    SupervisorError,
    draft_supervisor_decision,
    draft_worker_plan,
)


class SupervisorPlannerTests(unittest.TestCase):
    def _config(self, root: Path) -> None:
        (root / "agent-farm.config.json").write_text(
            json.dumps(
                {
                    "agent_backend": "codex",
                    "supervisor_model": "expensive-brain",
                    "worker_profiles": {
                        "cheap": {"model": "cheap-worker", "provider": "budget"},
                        "mid": {"model": "mid-worker", "provider": "budget"},
                    },
                    "default_worker_profile": "cheap",
                    "model_providers": {
                        "budget": {
                            "name": "Private budget endpoint",
                            "base_url": "https://private.example/v1",
                            "env_key": "BUDGET_SECRET",
                            "wire_api": "responses",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

    def test_expensive_supervisor_builds_valid_plan_without_worker_provider_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._config(root)

            def fake_run(args, cwd, **kwargs):
                output = Path(args[args.index("--output-last-message") + 1])
                output.write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "task_id": "desktop-task",
                            "base_ref": "HEAD",
                            "max_parallel": 2,
                            "deliverable": None,
                            "workers": [
                                {
                                    "id": "implementation",
                                    "role": "Implementation",
                                    "profile": "cheap",
                                    "goal": "Implement the scoped desktop change.",
                                    "allowed_paths": ["agent_farm/**"],
                                    "forbidden_paths": [],
                                    "test_commands": ["python -m unittest discover -s tests"],
                                    "acceptance": ["Tests pass"],
                                    "context": "Keep the change small.",
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                return CommandResult(args=args, cwd=str(cwd), returncode=0)

            with patch("agent_farm.supervisor.run_command", side_effect=fake_run) as mocked:
                plan = draft_worker_plan(
                    repo_root=root,
                    request="Build a desktop app.",
                    task_id="desktop-task",
                    worker_count=2,
                )

        args = mocked.call_args.args[0]
        prompt = mocked.call_args.kwargs["input_text"]
        encoded_args = " ".join(args)
        self.assertEqual(plan.workers[0].profile, "cheap")
        self.assertIn("expensive-brain", args)
        self.assertIn("cheap, mid", prompt)
        self.assertNotIn("cheap-worker", encoded_args)
        self.assertNotIn("private.example", encoded_args)
        self.assertNotIn("BUDGET_SECRET", encoded_args)
        self.assertIn("read-only", args)

    def test_rejects_supervisor_plan_with_unknown_worker_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._config(root)

            def fake_run(args, cwd, **kwargs):
                output = Path(args[args.index("--output-last-message") + 1])
                output.write_text(
                    json.dumps(
                        {
                            "task_id": "task",
                            "base_ref": "HEAD",
                            "deliverable": None,
                            "workers": [
                                {
                                    "id": "bad",
                                    "role": "Bad route",
                                    "profile": "premium-worker",
                                    "goal": "Use an unconfigured model.",
                                    "allowed_paths": ["src/**"],
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                return CommandResult(args=args, cwd=str(cwd), returncode=0)

            with patch("agent_farm.supervisor.run_command", side_effect=fake_run):
                with self.assertRaisesRegex(SupervisorError, "unknown worker profiles"):
                    draft_worker_plan(
                        repo_root=root,
                        request="Do work.",
                        task_id="task",
                    )

    def test_rejects_candidate_selection_when_user_requested_a_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._config(root)

            def fake_run(args, cwd, **kwargs):
                output = Path(args[args.index("--output-last-message") + 1])
                output.write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "task_id": "report-task",
                            "base_ref": "HEAD",
                            "max_parallel": 1,
                            "deliverable": None,
                            "workers": [
                                {
                                    "id": "candidate",
                                    "role": "Candidate",
                                    "profile": "cheap",
                                    "goal": "Build an unrelated candidate.",
                                    "allowed_paths": ["src/**"],
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                return CommandResult(args=args, cwd=str(cwd), returncode=0)

            with patch("agent_farm.supervisor.run_command", side_effect=fake_run):
                with self.assertRaisesRegex(SupervisorError, "collaborative deliverable"):
                    draft_worker_plan(
                        repo_root=root,
                        request="Combine both agents and write a report.",
                        task_id="report-task",
                    )

    def test_native_supervisor_reviews_real_farm_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            farm_dir = root / ".agent-farm" / "farms" / "farm-one"
            farm_dir.mkdir(parents=True)
            patch_file = farm_dir / "candidate.diff"
            patch_file.write_text("+safe change\n", encoding="utf-8")
            (root / "agent-farm.config.json").write_text(
                json.dumps(
                    {
                        "agent_backend": "native",
                        "supervisor_model": "expensive-brain",
                        "supervisor_provider": "local",
                        "model_providers": {
                            "local": {
                                "base_url": "http://127.0.0.1:8080/v1",
                                "wire_api": "responses",
                                "requires_openai_auth": False,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            (farm_dir / "review-package.json").write_text(
                json.dumps(
                    {
                        "task_id": "farm-one",
                        "workers": [
                            {
                                "id": "candidate",
                                "patch_file": str(patch_file),
                                "machine_review": {"status": "passed"},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            native_result = NativeAgentResult(
                ok=True,
                final_text="",
                terminal_payload={
                    "schema_version": 1,
                    "decision": "approve_merge",
                    "task_id": "farm-one",
                    "approved_worker": "candidate",
                    "risk_level": "low",
                    "reason": "The patch is scoped and machine review passed.",
                    "rollback_required": True,
                },
            )
            with patch("agent_farm.supervisor.run_native_agent", return_value=native_result) as mocked:
                decision = draft_supervisor_decision(repo_root=root, farm_dir=farm_dir)

            self.assertEqual(decision.approved_worker, "candidate")
            self.assertIn("candidate.diff", mocked.call_args.kwargs["prompt"])
            self.assertFalse(mocked.call_args.kwargs["writable"])


if __name__ == "__main__":
    unittest.main()
