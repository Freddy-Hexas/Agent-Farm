import json
import tempfile
import unittest
from pathlib import Path

from agent_farm.config import CONFIG_FILE, LOCAL_CONFIG_FILE, load_config, write_default_config


class ConfigTests(unittest.TestCase):
    def test_writes_and_loads_default_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / CONFIG_FILE
            write_default_config(path)
            config = load_config(Path(tmp), path)
        self.assertEqual(config.sandbox, "workspace-write")
        self.assertIn(".env", config.forbidden_paths)

    def test_rejects_unknown_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / CONFIG_FILE
            path.write_text(json.dumps({"surprise": True}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(Path(tmp), path)

    def test_loads_gitignored_local_config_over_public_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / CONFIG_FILE).write_text(json.dumps({"worker_model": "public"}), encoding="utf-8")
            (root / LOCAL_CONFIG_FILE).write_text(
                json.dumps({"worker_model": "local", "worker_provider": "proxy"}),
                encoding="utf-8",
            )
            config = load_config(root)
        self.assertEqual(config.worker_model, "local")
        self.assertEqual(config.worker_provider, "proxy")


if __name__ == "__main__":
    unittest.main()
