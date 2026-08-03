import tempfile
import unittest
from pathlib import Path

from agent_farm.crash_recovery import CrashRecoveryReporter
from agent_farm.util import read_json, write_json


class CrashRecoveryTests(unittest.TestCase):
    def test_stale_session_is_reported_and_clean_shutdown_removes_current_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_json(
                root / "active-session.json",
                {"session_id": "stale", "pid": 123, "started_at": "2026-08-01T00:00:00Z"},
            )
            reporter = CrashRecoveryReporter(root)

            report = reporter.start()
            reporter.record_reconciliation(3)

            self.assertTrue(report["detected"])
            self.assertEqual(report["previous_session_id"], "stale")
            self.assertEqual(read_json(root / "last-recovery.json")["interrupted_jobs"], 3)
            self.assertEqual(read_json(root / "active-session.json")["session_id"], reporter.session_id)
            reporter.mark_clean_shutdown()
            self.assertFalse((root / "active-session.json").exists())

    def test_clean_first_start_has_no_recovery_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reporter = CrashRecoveryReporter(Path(tmp))
            self.assertIsNone(reporter.start())
            reporter.mark_clean_shutdown()


if __name__ == "__main__":
    unittest.main()
