import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

from agent_farm.retention import ArtifactRetentionManager


class RetentionTests(unittest.TestCase):
    def test_backups_are_consistent_deduplicated_and_capped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / ".agent-farm"
            runtime.mkdir()
            database = runtime / "runtime.sqlite3"
            config = Path(tmp) / "agent-farm.local.json"
            config.write_text('{"worker_provider":"deepseek"}\n')
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("CREATE TABLE probe (value INTEGER)")
                connection.execute("INSERT INTO probe VALUES (1)")
                connection.commit()
            manager = ArtifactRetentionManager(runtime, retention_days=30, max_backups=2)

            first = manager.maintain(config_path=config)
            duplicate = manager.maintain(config_path=config)
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("INSERT INTO probe VALUES (2)")
                connection.commit()
            config.write_text('{"worker_provider":"deepseek","revision":2}\n')
            changed = manager.maintain(config_path=config)

            self.assertEqual(len(first["created"]), 2)
            self.assertEqual(duplicate["created"], [])
            self.assertEqual(len(changed["created"]), 2)
            self.assertLessEqual(len(list((runtime / "backups").glob("runtime-*.sqlite3"))), 2)
            newest = sorted((runtime / "backups").glob("runtime-*.sqlite3"))[-1]
            with closing(sqlite3.connect(newest)) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM probe").fetchone()[0], 2)

    def test_expired_runtime_artifacts_are_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            diagnostics = runtime / "diagnostics"
            submissions = runtime / "ui-submissions"
            diagnostics.mkdir()
            submissions.mkdir()
            old_files = [diagnostics / "old.zip", submissions / "old.json"]
            for path in old_files:
                path.write_text("old")
                os.utime(path, (time.time() - 3 * 86_400, time.time() - 3 * 86_400))

            result = ArtifactRetentionManager(runtime, retention_days=1).maintain()

            self.assertEqual(result["removed"], 2)
            self.assertTrue(all(not path.exists() for path in old_files))


if __name__ == "__main__":
    unittest.main()
