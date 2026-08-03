import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_farm.farm import FarmError, record_supervisor_decision, run_farm
from agent_farm.models import CommandResult
from agent_farm.plans import SupervisorDecision


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


class FarmTests(unittest.TestCase):
    def _repo_with_plan(self, root: Path) -> Path:
        git(root, "init")
        (root / "README.md").write_text("base\n", encoding="utf-8")
        git(root, "add", "README.md")
        git(root, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-m", "init")
        (root / "agent-farm.config.json").write_text(
            json.dumps(
                {
                    "worker_profiles": {
                        "cheap": {"model": "cheap-model", "provider": "budget"},
                        "mid": {"model": "mid-model", "provider": "quality"},
                    },
                    "max_parallel_workers": 2,
                    "auto_supervisor_review": False,
                }
            ),
            encoding="utf-8",
        )
        plan = root / "plan.json"
        plan.write_text(
            json.dumps(
                {
                    "task_id": "farm-test",
                    "workers": [
                        {
                            "id": "impl-a",
                            "role": "implementation",
                            "profile": "cheap",
                            "goal": "Implement A.",
                            "allowed_paths": ["src/a"],
                        },
                        {
                            "id": "impl-b",
                            "role": "implementation",
                            "profile": "mid",
                            "goal": "Implement B.",
                            "allowed_paths": ["src/b"],
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        return plan

    def test_runs_profiled_workers_and_builds_supervisor_review_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = self._repo_with_plan(root)

            def fake_run_task(**kwargs):
                worker_id = kwargs["task_file"].stem
                run_dir = root / ".agent-farm" / "runs" / worker_id
                return {
                    "status": "REVIEW_PENDING",
                    "run_dir": str(run_dir),
                    "worktree": str(root / ".agent-farm" / "worktrees" / worker_id),
                    "patch_file": str(run_dir / "patch.diff"),
                    "changed_files": [{"status": "M", "path": f"src/{worker_id}.py"}],
                    "tests": [],
                    "machine_review": {"status": "passed", "findings": []},
                }

            with patch("agent_farm.farm.run_task", side_effect=fake_run_task) as mocked:
                result = run_farm(repo=root, plan_file=plan)

            self.assertEqual(mocked.call_count, 2)
            self.assertEqual(result["status"], "SUPERVISOR_REVIEW_PENDING")
            self.assertEqual(set(result["passed_workers"]), {"impl-a", "impl-b"})
            self.assertEqual(result["profile_assignments"]["impl-a"]["model"], "cheap-model")
            self.assertEqual(result["profile_assignments"]["impl-b"]["provider"], "quality")
            review = json.loads(Path(result["review_package"]).read_text(encoding="utf-8"))
            self.assertEqual(review["supervisor_contract"]["authority"], "expensive-supervisor-only")

            decision_file = root / "decision.json"
            decision_file.write_text(
                json.dumps(
                    {
                        "decision": "approve_merge",
                        "task_id": result["farm_id"],
                        "approved_worker": "impl-a",
                        "risk_level": "low",
                        "reason": "Scoped patch and machine checks passed.",
                        "rollback_required": True,
                    }
                ),
                encoding="utf-8",
            )
            decided = record_supervisor_decision(Path(result["farm_dir"]), decision_file)
            self.assertEqual(decided["status"], "SUPERVISOR_APPROVED")

    def test_worker_dag_waits_for_dependencies_and_reports_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = self._repo_with_plan(root)
            payload = json.loads(plan.read_text(encoding="utf-8"))
            payload["workers"][1]["depends_on"] = ["impl-a"]
            plan.write_text(json.dumps(payload), encoding="utf-8")
            call_order = []
            events = []

            def fake_run_task(**kwargs):
                worker_id = kwargs["task_file"].stem
                call_order.append(worker_id)
                return {
                    "status": "REVIEW_PENDING",
                    "run_dir": str(root / ".agent-farm" / "runs" / worker_id),
                    "worktree": str(root / ".agent-farm" / "worktrees" / worker_id),
                    "patch_file": str(root / f"{worker_id}.diff"),
                    "changed_files": [],
                    "tests": [],
                    "machine_review": {"status": "passed", "findings": []},
                }

            with patch("agent_farm.farm.run_task", side_effect=fake_run_task):
                result = run_farm(repo=root, plan_file=plan, event_callback=events.append)

            self.assertEqual(call_order, ["impl-a", "impl-b"])
            self.assertEqual(result["passed_workers"], ["impl-a", "impl-b"])
            queued = [event for event in events if event["type"] == "worker.queued"]
            self.assertEqual(queued[1]["depends_on"], ["impl-a"])
            self.assertTrue(any(event.get("progress") == 100 for event in events))

    def test_native_farm_can_complete_automatic_supervisor_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = self._repo_with_plan(root)
            config_path = root / "agent-farm.config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["auto_supervisor_review"] = True
            config_path.write_text(json.dumps(config), encoding="utf-8")

            def fake_run_task(**kwargs):
                worker_id = kwargs["task_file"].stem
                run_dir = root / ".agent-farm" / "runs" / worker_id
                run_dir.mkdir(parents=True, exist_ok=True)
                patch_file = run_dir / "patch.diff"
                patch_file.write_text(f"patch for {worker_id}\n", encoding="utf-8")
                return {
                    "status": "REVIEW_PENDING",
                    "run_dir": str(run_dir),
                    "worktree": str(root / ".agent-farm" / "worktrees" / worker_id),
                    "patch_file": str(patch_file),
                    "changed_files": [{"status": "M", "path": f"src/{worker_id}.py"}],
                    "tests": [],
                    "machine_review": {"status": "passed", "findings": []},
                }

            def fake_review(*, farm_dir, **kwargs):
                return SupervisorDecision(
                    decision="approve_merge",
                    task_id=farm_dir.name,
                    approved_worker="impl-a",
                    risk_level="low",
                    reason="Candidate A is scoped and verified.",
                )

            with (
                patch("agent_farm.farm.run_task", side_effect=fake_run_task),
                patch("agent_farm.farm.draft_supervisor_decision", side_effect=fake_review),
            ):
                result = run_farm(repo=root, plan_file=plan)

            self.assertEqual(result["status"], "SUPERVISOR_APPROVED")
            self.assertEqual(result["decision"]["approved_worker"], "impl-a")

    def test_collaborative_farm_synthesizes_all_workers_into_deliverable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = self._repo_with_plan(root)
            raw = json.loads(plan.read_text(encoding="utf-8"))
            raw["deliverable"] = {
                "path": "report.md",
                "instructions": "Combine both Worker reports.",
            }
            plan.write_text(json.dumps(raw), encoding="utf-8")
            config_path = root / "agent-farm.config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["auto_supervisor_review"] = True
            config_path.write_text(json.dumps(config), encoding="utf-8")

            def fake_run_task(**kwargs):
                worker_id = kwargs["task_file"].stem
                run_dir = root / ".agent-farm" / "runs" / worker_id
                run_dir.mkdir(parents=True, exist_ok=True)
                patch_file = run_dir / "patch.diff"
                final_file = run_dir / "worker-final.md"
                patch_file.write_text(f"patch for {worker_id}\n", encoding="utf-8")
                final_file.write_text(f"brief from {worker_id}\n", encoding="utf-8")
                return {
                    "status": "REVIEW_PENDING",
                    "run_dir": str(run_dir),
                    "worktree": str(root / ".agent-farm" / "worktrees" / worker_id),
                    "patch_file": str(patch_file),
                    "worker": {"final_file": str(final_file)},
                    "changed_files": [{"status": "A", "path": f"src/{worker_id}.md"}],
                    "tests": [],
                    "machine_review": {"status": "passed", "findings": []},
                }

            def fake_synthesis(*, plan, **kwargs):
                output = root / plan.deliverable.path
                output.write_text("combined\n", encoding="utf-8")
                return {"path": str(output), "relative_path": plan.deliverable.path}

            with (
                patch("agent_farm.farm.run_task", side_effect=fake_run_task),
                patch(
                    "agent_farm.farm.synthesize_farm_deliverable",
                    side_effect=fake_synthesis,
                ) as synthesized,
            ):
                result = run_farm(repo=root, plan_file=plan)

            self.assertEqual(result["status"], "COMPLETED")
            self.assertEqual(result["deliverable"]["relative_path"], "report.md")
            self.assertEqual(synthesized.call_count, 1)

    def test_refuses_unscoped_cheap_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = self._repo_with_plan(root)
            raw = json.loads(plan.read_text(encoding="utf-8"))
            raw["workers"][0]["allowed_paths"] = []
            plan.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(FarmError, "no allowed_paths"):
                run_farm(repo=root, plan_file=plan)

    def test_failed_machine_review_escalates_to_configured_fallback_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = self._repo_with_plan(root)
            raw_plan = json.loads(plan.read_text(encoding="utf-8"))
            raw_plan["workers"] = [raw_plan["workers"][0]]
            plan.write_text(json.dumps(raw_plan), encoding="utf-8")
            config_path = root / "agent-farm.config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["worker_profiles"]["cheap"]["escalation_profile"] = "mid"
            config["max_worker_escalations"] = 1
            config_path.write_text(json.dumps(config), encoding="utf-8")
            profiles = []

            def fake_run_task(**kwargs):
                profiles.append(kwargs["profile"])
                profile = kwargs["profile"]
                run_dir = root / ".agent-farm" / "runs" / kwargs["task_id_override"]
                return {
                    "status": "REVIEW_PENDING",
                    "run_dir": str(run_dir),
                    "worktree": str(root / ".agent-farm" / "worktrees" / run_dir.name),
                    "patch_file": str(run_dir / "patch.diff"),
                    "changed_files": [{"status": "M", "path": "src/a.py"}],
                    "tests": [],
                    "machine_review": {
                        "status": "failed" if profile == "cheap" else "passed",
                        "findings": [],
                    },
                }

            with patch("agent_farm.farm.run_task", side_effect=fake_run_task):
                result = run_farm(repo=root, plan_file=plan)

            self.assertEqual(profiles, ["cheap", "mid"])
            self.assertEqual(result["status"], "SUPERVISOR_REVIEW_PENDING")
            record = result["workers"][0]
            self.assertEqual(record["profile"], "mid")
            self.assertEqual(record["model"], "mid-model")
            self.assertEqual(len(record["attempts"]), 2)
            self.assertEqual(record["attempts"][0]["machine_review"], "failed")
            self.assertEqual(record["attempts"][1]["machine_review"], "passed")

    def test_each_worker_receives_only_explicitly_assigned_attachments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = self._repo_with_plan(root)
            raw = json.loads(plan.read_text(encoding="utf-8"))
            raw["workers"][0]["attachments"] = ["att-a"]
            raw["workers"][1]["attachments"] = ["att-b"]
            plan.write_text(json.dumps(raw), encoding="utf-8")
            observed = {}

            def fake_run_task(**kwargs):
                worker_id = kwargs["task_file"].stem
                observed[worker_id] = {
                    "context": kwargs["attachment_context"],
                    "images": kwargs["model_attachments"],
                }
                run_dir = root / ".agent-farm" / "runs" / worker_id
                return {
                    "status": "REVIEW_PENDING",
                    "run_dir": str(run_dir),
                    "worktree": str(root / ".agent-farm" / "worktrees" / worker_id),
                    "patch_file": str(run_dir / "patch.diff"),
                    "changed_files": [],
                    "tests": [],
                    "machine_review": {"status": "passed", "findings": []},
                }

            with patch("agent_farm.farm.run_task", side_effect=fake_run_task):
                run_farm(
                    repo=root,
                    plan_file=plan,
                    attachment_context="all attachments must not be broadcast",
                    model_attachments=[{"name": "all.png", "data_url": "data:image/png;base64,ALL"}],
                    attachment_contexts={"att-a": "context A", "att-b": "context B"},
                    model_attachments_by_id={
                        "att-a": {"name": "a.png", "data_url": "data:image/png;base64,A"},
                        "att-b": {"name": "b.png", "data_url": "data:image/png;base64,B"},
                    },
                )

            self.assertEqual(observed["impl-a"]["context"], "context A")
            self.assertEqual(observed["impl-b"]["context"], "context B")
            self.assertEqual(observed["impl-a"]["images"][0]["name"], "a.png")
            self.assertEqual(observed["impl-b"]["images"][0]["name"], "b.png")
            self.assertNotIn("all attachments", observed["impl-a"]["context"])

    def test_real_orchestration_uses_separate_worktrees_for_parallel_workers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = self._repo_with_plan(root)

            def fake_codex_worker(*, paths, **kwargs):
                worker_id = paths.worktree.name.rsplit("-", 1)[-1]
                relative = Path("src/a/result.txt") if worker_id == "a" else Path("src/b/result.txt")
                target = paths.worktree / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(f"worker={worker_id}\n", encoding="utf-8")
                paths.worker_final_file.write_text("done\n", encoding="utf-8")
                return CommandResult(args=["fake-codex"], cwd=str(paths.worktree), returncode=0)

            with patch("agent_farm.orchestrator.run_codex_worker", side_effect=fake_codex_worker):
                result = run_farm(repo=root, plan_file=plan)

            self.assertEqual(result["status"], "SUPERVISOR_REVIEW_PENDING")
            self.assertEqual(len(result["workers"]), 2)
            changed = {
                item["id"]: [entry["path"] for entry in item["changed_files"]]
                for item in result["workers"]
            }
            self.assertEqual(changed["impl-a"], ["src/a/result.txt"])
            self.assertEqual(changed["impl-b"], ["src/b/result.txt"])

    def test_supervisor_cannot_approve_failed_machine_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            farm_dir = Path(tmp)
            (farm_dir / "result.json").write_text(
                json.dumps(
                    {
                        "farm_id": "farm-1",
                        "workers": [
                            {
                                "id": "bad-worker",
                                "machine_review": {"status": "failed"},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            decision_file = farm_dir / "input-decision.json"
            decision_file.write_text(
                json.dumps(
                    {
                        "decision": "approve_merge",
                        "task_id": "farm-1",
                        "approved_worker": "bad-worker",
                        "risk_level": "low",
                        "reason": "This must be rejected by the deterministic gate.",
                        "rollback_required": True,
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(FarmError, "failed machine review"):
                record_supervisor_decision(farm_dir, decision_file)


if __name__ == "__main__":
    unittest.main()
