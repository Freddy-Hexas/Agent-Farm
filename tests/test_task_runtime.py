import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_farm.plans import WorkerPlan
from agent_farm.web_server import ConsoleState


class TaskRuntimeTests(unittest.TestCase):
    def _repo(self, root: Path) -> None:
        (root / ".git").mkdir()
        (root / "agent-farm.local.json").write_text(
            '{"supervisor_model":"planner","supervisor_provider":"openai",'
            '"worker_profiles":{"cheap":{"model":"worker","provider":"openai"}},'
            '"default_worker_profile":"cheap"}',
            encoding="utf-8",
        )

    def test_task_runs_planner_and_farm_and_persists_ordered_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            state = ConsoleState(root, None)
            plan = WorkerPlan.from_dict(
                {
                    "schema_version": 1,
                    "task_id": "real-task",
                    "base_ref": "HEAD",
                    "max_parallel": 1,
                    "workers": [
                        {
                            "id": "worker-a",
                            "role": "research",
                            "profile": "cheap",
                            "complexity": "simple",
                            "attachments": [],
                            "depends_on": [],
                            "goal": "Collect evidence",
                            "allowed_paths": ["reports/**"],
                            "forbidden_paths": [],
                            "test_commands": [],
                            "acceptance": ["Evidence is written"],
                            "context": "",
                        }
                    ],
                    "deliverable": None,
                }
            )

            def fake_farm(**kwargs):
                kwargs["event_callback"]({"type": "worker.completed", "status": "completed"})
                return {"farm_id": "farm-real-task", "status": "COMPLETED", "workers": []}

            try:
                with (
                    patch("agent_farm.task_runtime.draft_worker_plan", return_value=plan),
                    patch("agent_farm.task_runtime.run_farm", side_effect=fake_farm),
                ):
                    job = state.tasks.submit({"request": "Do the real task", "worker_count": 1})
                    deadline = time.monotonic() + 3
                    while state.tasks.get(job["job_id"])["status"] not in {"COMPLETED", "FAILED"}:
                        if time.monotonic() > deadline:
                            self.fail("task did not settle")
                        time.sleep(0.01)
                    settled = state.tasks.get(job["job_id"])
                    self.assertEqual(settled["status"], "COMPLETED")
                    events = state.tasks.events(job["job_id"])["events"]
                    self.assertEqual([event["sequence"] for event in events], list(range(1, len(events) + 1)))
                    self.assertIn("task.plan.ready", [event["type"] for event in events])
                    self.assertIn("worker.completed", [event["type"] for event in events])
                    self.assertEqual(events[-1]["type"], "task.completed")
            finally:
                state.close()

    def test_incomplete_farm_is_a_failed_task_with_a_visible_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            state = ConsoleState(root, None)
            plan = WorkerPlan.from_dict(
                {
                    "schema_version": 1,
                    "task_id": "incomplete-task",
                    "workers": [
                        {
                            "id": "worker-a",
                            "role": "research",
                            "profile": "cheap",
                            "goal": "Collect evidence",
                            "allowed_paths": ["reports/**"],
                        }
                    ],
                }
            )
            try:
                with (
                    patch("agent_farm.task_runtime.draft_worker_plan", return_value=plan),
                    patch(
                        "agent_farm.task_runtime.run_farm",
                        return_value={"farm_id": "farm-incomplete", "status": "REVISION_REQUESTED"},
                    ),
                ):
                    job = state.tasks.submit({"request": "Do the task", "worker_count": 1})
                    deadline = time.monotonic() + 3
                    while state.tasks.get(job["job_id"])["status"] not in {"COMPLETED", "FAILED"}:
                        if time.monotonic() > deadline:
                            self.fail("task did not settle")
                        time.sleep(0.01)
                    settled = state.tasks.get(job["job_id"])
                    self.assertEqual(settled["status"], "FAILED")
                    self.assertEqual(settled["error"]["type"], "FarmIncomplete")
            finally:
                state.close()

    def test_resume_reuses_persisted_plan_without_replanning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            state = ConsoleState(root, None)
            plan = WorkerPlan.from_dict(
                {
                    "schema_version": 1,
                    "task_id": "resume-task",
                    "workers": [
                        {
                            "id": "worker-a",
                            "role": "research",
                            "profile": "cheap",
                            "goal": "Collect evidence",
                            "allowed_paths": ["reports/**"],
                        }
                    ],
                }
            )
            farm_calls = 0

            def fake_farm(**kwargs):
                nonlocal farm_calls
                farm_calls += 1
                if farm_calls == 1:
                    return {"farm_id": "farm-first", "status": "REVISION_REQUESTED"}
                return {"farm_id": "farm-resumed", "status": "COMPLETED"}

            try:
                with (
                    patch("agent_farm.task_runtime.draft_worker_plan", return_value=plan) as planner,
                    patch("agent_farm.task_runtime.run_farm", side_effect=fake_farm),
                ):
                    job = state.tasks.submit({"request": "Do the task", "worker_count": 1})
                    deadline = time.monotonic() + 3
                    while state.tasks.get(job["job_id"])["status"] not in {"COMPLETED", "FAILED"}:
                        if time.monotonic() > deadline:
                            self.fail("task did not settle")
                        time.sleep(0.01)
                    self.assertEqual(state.tasks.get(job["job_id"])["status"], "FAILED")

                    resumed = state.tasks.resume(job["job_id"])
                    self.assertEqual(resumed["resume_from"], "workers")
                    deadline = time.monotonic() + 3
                    while state.tasks.get(job["job_id"])["status"] != "COMPLETED":
                        if time.monotonic() > deadline:
                            self.fail("resumed task did not settle")
                        time.sleep(0.01)

                    self.assertEqual(planner.call_count, 1)
                    self.assertEqual(farm_calls, 2)
                    events = state.tasks.events(job["job_id"])["events"]
                    self.assertIn("task.plan.reused", [event["type"] for event in events])
                    self.assertEqual(events[-1]["type"], "task.completed")
            finally:
                state.close()


if __name__ == "__main__":
    unittest.main()
