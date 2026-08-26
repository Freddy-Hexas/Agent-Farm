import tempfile
import unittest
from pathlib import Path

from agent_farm.runtime_store import RuntimeStore
from agent_farm.subagents import (
    ChildSessionService,
    SessionAuthorizationError,
    scrub_child_environment,
)


class SessionLedgerTests(unittest.TestCase):
    def test_job_events_are_mirrored_and_worker_lineage_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RuntimeStore(Path(tmp) / "runtime.sqlite3")
            store.create_job(
                "farm",
                {
                    "job_id": "farm-job",
                    "session_id": "session-farm-job",
                    "parent_session_id": "thread-parent",
                    "role": "farm",
                    "status": "RUNNING",
                    "created_at": "2026-08-25T00:00:00+00:00",
                },
            )
            store.append_event(
                "farm",
                "farm-job",
                {
                    "type": "worker.completed",
                    "agent_id": "worker-a",
                    "agent_kind": "worker",
                    "session_id": "session-worker-a",
                    "parent_session_id": "session-farm-job",
                    "status": "completed",
                    "timestamp": "2026-08-25T00:00:01+00:00",
                },
            )
            worker = store.get_session("session-worker-a")
            self.assertEqual(worker["parent_session_id"], "session-farm-job")
            self.assertEqual(worker["role"], "worker")
            self.assertEqual(store.session_events("session-worker-a")[0]["event_type"], "worker.completed")
            projected = store.project_job("farm", "farm-job")
            self.assertEqual(projected["status"], "RUNNING")
            self.assertEqual(projected["event_count"], 1)

    def test_task_lifecycle_events_rebuild_supervisor_session_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RuntimeStore(Path(tmp) / "runtime.sqlite3")
            store.create_job(
                "task",
                {
                    "job_id": "task-job",
                    "session_id": "session-task-job",
                    "role": "supervisor",
                    "status": "RUNNING",
                    "created_at": "2026-08-25T00:00:00+00:00",
                },
            )
            store.append_event(
                "task",
                "task-job",
                {
                    "type": "task.phase",
                    "status": "running",
                    "phase": "workers",
                    "timestamp": "2026-08-25T00:00:01+00:00",
                },
            )
            store.append_event(
                "task",
                "task-job",
                {
                    "type": "task.completed",
                    "status": "completed",
                    "timestamp": "2026-08-25T00:00:02+00:00",
                },
            )

            session = store.get_session("session-task-job")
            self.assertEqual(session["status"], "completed")
            self.assertEqual(store.session_projection("session-task-job")["stop_reason"], "completed")
            self.assertEqual(store.project_job("task", "task-job")["status"], "COMPLETED")

    def test_session_events_are_contiguous_and_projection_is_rebuildable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RuntimeStore(Path(tmp) / "runtime.sqlite3")
            store.create_session(
                {
                    "session_id": "session-parent",
                    "role": "supervisor",
                    "status": "running",
                    "created_at": "2026-08-25T00:00:00+00:00",
                }
            )
            for event_type in ("session/started", "model.delta", "session/completed"):
                store.append_session_event(
                    "session-parent",
                    {"type": event_type, "timestamp": "2026-08-25T00:00:01+00:00"},
                )

            reopened = RuntimeStore(Path(tmp) / "runtime.sqlite3")
            events = reopened.session_events("session-parent")
            self.assertEqual([event["event_seq"] for event in events], [1, 2, 3])
            self.assertEqual([event["sequence"] for event in events], [1, 2, 3])
            projection = reopened.session_projection("session-parent")
            self.assertEqual(projection["event_count"], 3)
            self.assertEqual(projection["status"], "completed")
            self.assertEqual(projection["stop_reason"], "completed")
            self.assertEqual(reopened.get_session("session-parent")["status"], "completed")

    def test_child_session_lifecycle_and_lineage_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RuntimeStore(Path(tmp) / "runtime.sqlite3")
            store.create_session(
                {
                    "session_id": "session-supervisor",
                    "role": "supervisor",
                    "status": "running",
                    "created_at": "2026-08-25T00:00:00+00:00",
                }
            )
            service = ChildSessionService(store)
            child = service.spawn(
                "session-supervisor",
                provider_id="deepseek",
                model_id="deepseek-chat",
                request="Collect evidence",
            )
            self.assertEqual(child["parent_session_id"], "session-supervisor")
            self.assertEqual(child["status"], "queued")
            with self.assertRaises(SessionAuthorizationError):
                service.cancel(child["session_id"], actor_session_id="other-session")
            cancelled = service.cancel(child["session_id"])
            self.assertEqual(cancelled["stop_reason"], "cancelled")
            report = service.report(child["session_id"])
            self.assertEqual(report["stop_reason"], "cancelled")
            self.assertNotIn("timeline", report)

    def test_child_environment_scrubs_credentials_unless_explicitly_allowed(self) -> None:
        values = {
            "PATH": "path",
            "DEEPSEEK_API_KEY": "secret",
            "NORMAL_FLAG": "1",
        }
        scrubbed = scrub_child_environment(values)
        self.assertEqual(scrubbed, {"PATH": "path", "NORMAL_FLAG": "1"})
        allowed = scrub_child_environment(values, allowed_keys={"DEEPSEEK_API_KEY"})
        self.assertEqual(allowed["DEEPSEEK_API_KEY"], "secret")


if __name__ == "__main__":
    unittest.main()
