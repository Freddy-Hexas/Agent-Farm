from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_farm.secrets import load_secrets_env, update_secrets_env


class SecretsTests(unittest.TestCase):
    def test_update_merges_and_atomically_replaces_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / ".agent-farm" / "secrets.env"
            path.parent.mkdir(parents=True)
            path.write_text("# keep this comment\nEXISTING=value\nTARGET=old\n", encoding="utf-8")

            updated = update_secrets_env(
                root,
                ".agent-farm/secrets.env",
                {"TARGET": "new=value", "ADDED": "secret-token"},
            )

            self.assertEqual(updated, path.resolve())
            self.assertEqual(
                load_secrets_env(root, ".agent-farm/secrets.env"),
                {"EXISTING": "value", "TARGET": "new=value", "ADDED": "secret-token"},
            )
            self.assertIn("# keep this comment", path.read_text(encoding="utf-8"))

    def test_update_rejects_injection_and_paths_outside_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            with self.assertRaisesRegex(ValueError, "control character"):
                update_secrets_env(root, ".agent-farm/secrets.env", {"API_KEY": "key\nEVIL=yes"})
            with self.assertRaisesRegex(ValueError, "inside the repository"):
                update_secrets_env(root, str(root.parent / "outside.env"), {"API_KEY": "safe"})


if __name__ == "__main__":
    unittest.main()
