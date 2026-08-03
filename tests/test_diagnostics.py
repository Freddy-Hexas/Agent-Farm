import json
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path

from agent_farm.diagnostics import StructuredLogger, create_diagnostic_bundle, redact


class DiagnosticsTests(unittest.TestCase):
    def test_recursive_redaction_removes_keys_and_authorization_values(self) -> None:
        result = redact(
            {
                "api_key": "sk-secret-value",
                "nested": {"Authorization": "Bearer top-secret-token"},
                "message": "request used token-abcdefghijk",
                "safe": "deepseek-chat",
            }
        )
        encoded = json.dumps(result)
        self.assertNotIn("top-secret", encoded)
        self.assertNotIn("abcdefghijk", encoded)
        self.assertNotIn("sk-secret", encoded)
        self.assertEqual(result["safe"], "deepseek-chat")

    def test_structured_log_and_exported_bundle_are_safe_and_inspectable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / ".agent-farm"
            logger = StructuredLogger(runtime / "logs" / "events.jsonl")
            logger.log(
                "provider.request",
                correlation_id="correlation-123",
                provider="deepseek",
                authorization="Bearer private-value-123",
            )
            with closing(sqlite3.connect(runtime / "runtime.sqlite3")) as connection:
                connection.executescript(
                    """
                    CREATE TABLE runtime_jobs (kind TEXT, status TEXT);
                    CREATE TABLE runtime_events (sequence INTEGER);
                    PRAGMA user_version = 2;
                    INSERT INTO runtime_jobs VALUES ('farm', 'COMPLETED');
                    INSERT INTO runtime_events VALUES (1);
                    """
                )
                connection.commit()
            (runtime / "secrets.env").write_text("DEEPSEEK_API_KEY=never-export\n")

            bundle = create_diagnostic_bundle(
                root,
                sanitized_config={"worker_provider": "deepseek", "api_key": "never-export"},
            )

            self.assertTrue(Path(bundle["path"]).is_file())
            with zipfile.ZipFile(bundle["path"]) as archive:
                names = set(archive.namelist())
                self.assertEqual(
                    names,
                    {"manifest.json", "config.sanitized.json", "runtime-summary.json", "events.jsonl"},
                )
                exported = "\n".join(
                    archive.read(name).decode("utf-8") for name in sorted(names)
                )
            self.assertIn("correlation-123", exported)
            self.assertIn("deepseek", exported)
            self.assertNotIn("never-export", exported)
            self.assertNotIn("private-value", exported)


if __name__ == "__main__":
    unittest.main()
