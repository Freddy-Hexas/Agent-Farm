import tempfile
import unittest
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from agent_farm.runtime_store import RUNTIME_SCHEMA_VERSION, RuntimeStore


class RuntimeStoreTests(unittest.TestCase):
    def test_correlation_id_flows_from_job_to_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RuntimeStore(Path(tmp) / "runtime.sqlite3")
            store.create_job(
                "plan",
                {
                    "job_id": "correlated-plan",
                    "status": "QUEUED",
                    "created_at": "2026-08-03T00:00:00+00:00",
                    "correlation_id": "request-123",
                },
            )
            event = store.append_event(
                "plan",
                "correlated-plan",
                {"type": "model.delta", "timestamp": "2026-08-03T00:00:01+00:00"},
            )
            self.assertEqual(event["correlation_id"], "request-123")
            self.assertEqual(
                store.events("plan", "correlated-plan")[0]["correlation_id"],
                "request-123",
            )

    def test_jobs_and_ordered_events_survive_new_store_instances(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runtime.sqlite3"
            store = RuntimeStore(path)
            store.create_job(
                "farm",
                {
                    "job_id": "farm-one",
                    "status": "QUEUED",
                    "created_at": "2026-08-02T01:00:00+00:00",
                    "started_at": None,
                    "finished_at": None,
                    "error": None,
                },
            )
            store.update_job(
                "farm",
                "farm-one",
                {"status": "COMPLETED", "finished_at": "2026-08-02T01:01:00+00:00"},
                updated_at="2026-08-02T01:01:00+00:00",
            )
            first = store.append_event(
                "farm",
                "farm-one",
                {"type": "worker.started", "timestamp": "2026-08-02T01:00:01+00:00"},
            )
            second = store.append_event(
                "farm",
                "farm-one",
                {"type": "worker.completed", "timestamp": "2026-08-02T01:00:59+00:00"},
            )

            reopened = RuntimeStore(path)
            job = reopened.get_job("farm", "farm-one")
            events = reopened.events("farm", "farm-one", after=1)

            self.assertEqual(job["status"], "COMPLETED")
            self.assertEqual(first["sequence"], 1)
            self.assertEqual(second["sequence"], 2)
            self.assertEqual([event["type"] for event in events], ["worker.completed"])
            self.assertEqual(reopened.recent_jobs("farm")[0]["job_id"], "farm-one")

    def test_restart_interrupts_only_active_jobs_and_records_an_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RuntimeStore(Path(tmp) / "runtime.sqlite3")
            for job_id, status in (
                ("queued-plan", "QUEUED"),
                ("running-plan", "RUNNING"),
                ("complete-plan", "COMPLETED"),
            ):
                store.create_job(
                    "plan",
                    {
                        "job_id": job_id,
                        "status": status,
                        "created_at": f"2026-08-02T01:00:0{len(job_id)}+00:00",
                        "started_at": None,
                        "finished_at": None,
                        "error": None,
                    },
                )

            recovered = store.interrupt_active_jobs(
                "plan",
                interrupted_at="2026-08-02T02:00:00+00:00",
                message="Runtime restarted.",
            )

            self.assertEqual({job["job_id"] for job in recovered}, {"queued-plan", "running-plan"})
            self.assertEqual(store.get_job("plan", "complete-plan")["status"], "COMPLETED")
            for job_id in ("queued-plan", "running-plan"):
                job = store.get_job("plan", job_id)
                events = store.events("plan", job_id)
                self.assertEqual(job["status"], "INTERRUPTED")
                self.assertEqual(job["error"]["type"], "RuntimeInterrupted")
                self.assertEqual(events[-1]["type"], "runtime.interrupted")

    def test_legacy_database_is_migrated_without_losing_jobs_or_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runtime.sqlite3"
            job = {
                "job_id": "legacy-job",
                "status": "COMPLETED",
                "created_at": "2026-01-01T00:00:00+00:00",
            }
            event = {
                "type": "worker.completed",
                "timestamp": "2026-01-01T00:00:01+00:00",
                "sequence": 1,
            }
            with closing(sqlite3.connect(path)) as connection:
                connection.executescript(
                    """
                    CREATE TABLE runtime_jobs (
                        kind TEXT NOT NULL, job_id TEXT NOT NULL, status TEXT NOT NULL,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                        payload_json TEXT NOT NULL, PRIMARY KEY (kind, job_id)
                    );
                    CREATE TABLE runtime_events (
                        kind TEXT NOT NULL, job_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                        timestamp TEXT NOT NULL, payload_json TEXT NOT NULL,
                        PRIMARY KEY (kind, job_id, sequence)
                    );
                    PRAGMA user_version = 1;
                    """
                )
                connection.execute(
                    "INSERT INTO runtime_jobs VALUES (?, ?, ?, ?, ?, ?)",
                    ("farm", "legacy-job", "COMPLETED", job["created_at"], job["created_at"], json.dumps(job)),
                )
                connection.execute(
                    "INSERT INTO runtime_events VALUES (?, ?, ?, ?, ?)",
                    ("farm", "legacy-job", 1, event["timestamp"], json.dumps(event)),
                )
                connection.commit()

            store = RuntimeStore(path)
            self.assertEqual(store.get_job("farm", "legacy-job")["status"], "COMPLETED")
            self.assertEqual(store.events("farm", "legacy-job")[0]["type"], "worker.completed")
            store.append_event(
                "farm",
                "legacy-job",
                {"type": "review.completed", "timestamp": "2026-01-01T00:00:02+00:00"},
            )
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], RUNTIME_SCHEMA_VERSION)
                job_columns = {row[1] for row in connection.execute("PRAGMA table_info(runtime_jobs)")}
                event_columns = {row[1] for row in connection.execute("PRAGMA table_info(runtime_events)")}
                indexes = {row[1] for row in connection.execute("PRAGMA index_list(runtime_events)")}
            self.assertIn("correlation_id", job_columns)
            self.assertIn("correlation_id", event_columns)
            self.assertIn("runtime_events_correlation", indexes)

    def test_newer_database_schema_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runtime.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(f"PRAGMA user_version = {RUNTIME_SCHEMA_VERSION + 1}")
                connection.commit()
            with self.assertRaisesRegex(RuntimeError, "newer than supported"):
                RuntimeStore(path)


if __name__ == "__main__":
    unittest.main()
