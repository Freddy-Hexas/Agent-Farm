import json
import tempfile
import unittest
from pathlib import Path

from agent_farm.config import (
    CONFIG_FILE,
    LOCAL_CONFIG_FILE,
    default_config,
    load_config,
    resolve_worker_profile,
    write_local_config,
    write_default_config,
)


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

    def test_resolves_cheap_worker_profile_without_changing_supervisor_contract(self):
        config = default_config()
        data = config.to_json()
        data["worker_profiles"] = {
            "cheap": {
                "display_name": "Economy Worker",
                "model": "cheap-model",
                "provider": "budget-provider",
                "timeout_seconds": 90,
                "codex_config_overrides": {"model_reasoning_effort": "low"},
            }
        }
        from agent_farm.models import AgentFarmConfig

        resolved, selected = resolve_worker_profile(AgentFarmConfig.from_dict(data), "cheap")
        self.assertEqual(selected, "cheap")
        self.assertEqual(config.worker_profiles, {})
        self.assertEqual(resolved.worker_model, "cheap-model")
        self.assertEqual(resolved.worker_provider, "budget-provider")
        self.assertEqual(resolved.timeout_seconds, 90)
        self.assertEqual(resolved.codex_config_overrides["model_reasoning_effort"], "low")

    def test_rejects_unknown_worker_profile(self):
        config = default_config()
        with self.assertRaisesRegex(ValueError, "Unknown worker profile"):
            resolve_worker_profile(config, "missing")

    def test_local_settings_are_validated_and_written_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = default_config().to_json()
            data.update(
                {
                    "supervisor_model": "expensive-model",
                    "default_worker_profile": "cheap",
                    "worker_profiles": {
                        "cheap": {
                            "model": "cheap-model",
                            "codex_config_overrides": {"model_reasoning_effort": "low"},
                        }
                    },
                }
            )
            path = write_local_config(root, data)
            loaded = load_config(root)

            self.assertEqual(path, root / LOCAL_CONFIG_FILE)
            self.assertEqual(loaded.supervisor_model, "expensive-model")
            self.assertEqual(loaded.worker_profiles["cheap"]["model"], "cheap-model")
            self.assertEqual(list(root.glob(".*.tmp")), [])

            data["sandbox"] = "invalid"
            with self.assertRaisesRegex(ValueError, "sandbox"):
                write_local_config(root, data)


if __name__ == "__main__":
    unittest.main()
