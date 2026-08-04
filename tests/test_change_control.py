from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_farm.change_control import ChangeControlError, ChangeController, build_change_set
from agent_farm.checkpoints import CheckpointError, CheckpointStore
from agent_farm.models import AgentFarmConfig, TestResult as FarmTestResult


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


class ChangeControlTests(unittest.TestCase):
    def _repo(self, root: Path) -> None:
        git(root, "init")
        (root / "app.txt").write_text("base\n")
        git(root, "add", "app.txt")
        git(root, "-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-m", "base")

    def _farm(self, root: Path, *, binary: bool = False) -> tuple[Path, dict]:
        farm_dir = root / ".agent-farm" / "farms" / "farm-test"
        run_dir = root / ".agent-farm" / "runs" / "worker-one"
        farm_dir.mkdir(parents=True)
        run_dir.mkdir(parents=True)
        if binary:
            target = root / "asset.bin"
            target.write_bytes(b"\x00base\x01")
            git(root, "add", "asset.bin")
            git(root, "-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-m", "binary")
            target.write_bytes(b"\x00changed\x02")
            changed_files = [{"status": "M", "path": "asset.bin"}]
        else:
            (root / "app.txt").write_text("candidate\n")
            changed_files = [{"status": "M", "path": "app.txt"}]
        patch_file = farm_dir / "worker-one.patch"
        patch_file.write_text(git(root, "diff", "--binary"), newline="")
        git(root, "checkout", "--", changed_files[0]["path"])
        worker_result = {
            "config": AgentFarmConfig(test_commands=[]).to_json(),
        }
        (run_dir / "result.json").write_text(json.dumps(worker_result))
        result = {
            "schema_version": 1,
            "farm_id": "farm-test",
            "base_commit": git(root, "rev-parse", "HEAD").strip(),
            "status": "SUPERVISOR_APPROVED",
            "decision": {
                "decision": "approve_merge",
                "approved_worker": "one",
            },
            "workers": [
                {
                    "id": "one",
                    "role": "implementation",
                    "provider": "test",
                    "model": "test-model",
                    "status": "SUPERVISOR_REVIEW_PENDING",
                    "run_dir": str(run_dir),
                    "patch_file": str(patch_file),
                    "changed_files": changed_files,
                    "tests": [],
                    "machine_review": {"status": "passed"},
                }
            ],
        }
        (farm_dir / "result.json").write_text(json.dumps(result))
        return farm_dir, result

    def test_checkpoint_apply_merge_and_rollback_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            farm_dir, _ = self._farm(root)
            checkpoints = CheckpointStore(root, root / ".agent-farm" / "checkpoints")
            controller = ChangeController(root, checkpoints)

            applied = controller.apply(farm_dir, "one")
            checkpoint_id = applied["change_control"]["checkpoint_id"]
            self.assertEqual(applied["status"], "VERIFIED")
            self.assertEqual((root / "app.txt").read_text(), "candidate\n")
            self.assertEqual(checkpoints.read(checkpoint_id)["status"], "VERIFIED")

            merged = controller.merge(farm_dir)
            self.assertEqual(merged["status"], "MERGED")
            self.assertEqual(checkpoints.read(checkpoint_id)["status"], "MERGED")

            rolled_back = controller.rollback(farm_dir, checkpoint_id)
            self.assertEqual(rolled_back["status"], "ROLLED_BACK")
            self.assertEqual((root / "app.txt").read_text(), "base\n")
            self.assertEqual(checkpoints.read(checkpoint_id)["status"], "ROLLED_BACK")

    def test_rollback_refuses_to_overwrite_edits_created_after_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            farm_dir, _ = self._farm(root)
            checkpoints = CheckpointStore(root, root / ".agent-farm" / "checkpoints")
            controller = ChangeController(root, checkpoints)
            applied = controller.apply(farm_dir, "one")
            checkpoint_id = applied["change_control"]["checkpoint_id"]
            (root / "app.txt").write_text("user edit after apply\n")

            with self.assertRaisesRegex(CheckpointError, "changed after"):
                controller.rollback(farm_dir, checkpoint_id)
            controller.rollback(farm_dir, checkpoint_id, force=True)
            self.assertEqual((root / "app.txt").read_text(), "base\n")

    def test_apply_rejects_dirty_overlap_and_unapproved_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            farm_dir, _ = self._farm(root)
            checkpoints = CheckpointStore(root, root / ".agent-farm" / "checkpoints")
            controller = ChangeController(root, checkpoints)
            (root / "app.txt").write_text("user dirty\n")
            with self.assertRaisesRegex(ChangeControlError, "already contain"):
                controller.apply(farm_dir, "one")

            result_file = farm_dir / "result.json"
            result = json.loads(result_file.read_text())
            result["decision"] = {"decision": "reject", "approved_worker": None}
            result_file.write_text(json.dumps(result))
            with self.assertRaisesRegex(ChangeControlError, "Supervisor approval"):
                controller.apply(farm_dir, "one")

    def test_binary_change_set_is_typed_and_reversible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            farm_dir, result = self._farm(root, binary=True)
            change_set = build_change_set(root, result, "one")
            self.assertTrue(change_set["binary"])
            self.assertTrue(change_set["files"][0]["binary"])

            checkpoints = CheckpointStore(root, root / ".agent-farm" / "checkpoints")
            controller = ChangeController(root, checkpoints)
            applied = controller.apply(farm_dir, "one")
            checkpoint_id = applied["change_control"]["checkpoint_id"]
            self.assertEqual((root / "asset.bin").read_bytes(), b"\x00changed\x02")
            controller.rollback(farm_dir, checkpoint_id)
            self.assertEqual((root / "asset.bin").read_bytes(), b"\x00base\x01")

    def test_failed_post_apply_verification_rolls_back_automatically(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            farm_dir, _ = self._farm(root)
            run_result_path = root / ".agent-farm" / "runs" / "worker-one" / "result.json"
            run_result = json.loads(run_result_path.read_text())
            run_result["config"]["test_commands"] = ["python -m pytest"]
            run_result_path.write_text(json.dumps(run_result))
            checkpoints = CheckpointStore(root, root / ".agent-farm" / "checkpoints")
            controller = ChangeController(root, checkpoints)
            failure = FarmTestResult("python -m pytest", 1, "failed.log", 0.1)

            with patch("agent_farm.change_control._run_tests", return_value=[failure]):
                with self.assertRaisesRegex(ChangeControlError, "rolled back"):
                    controller.apply(farm_dir, "one")
            self.assertEqual((root / "app.txt").read_text(), "base\n")
            result = json.loads((farm_dir / "result.json").read_text())
            self.assertEqual(result["change_control"]["status"], "ROLLED_BACK")

    def test_checkpoint_retention_prunes_old_inactive_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._repo(root)
            patch_file = root / ".agent-farm" / "candidate.patch"
            patch_file.parent.mkdir()
            patch_file.write_text("")
            checkpoints = CheckpointStore(
                root,
                root / ".agent-farm" / "checkpoints",
                retention=1,
            )
            with patch(
                "agent_farm.checkpoints._utc_now",
                return_value="2026-08-04T00:00:00+00:00",
            ):
                first = checkpoints.create(
                    farm_id="farm-one",
                    worker_id="one",
                    affected_paths=["app.txt"],
                    patch_file=patch_file,
                    base_commit="base",
                )
                second = checkpoints.create(
                    farm_id="farm-two",
                    worker_id="two",
                    affected_paths=["app.txt"],
                    patch_file=patch_file,
                    base_commit="base",
                )
            self.assertFalse(
                (root / ".agent-farm" / "checkpoints" / first["checkpoint_id"]).exists()
            )
            self.assertTrue(
                (root / ".agent-farm" / "checkpoints" / second["checkpoint_id"]).exists()
            )


if __name__ == "__main__":
    unittest.main()
