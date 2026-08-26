from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_farm.models import AgentFarmConfig, RunPaths
from agent_farm.orchestrator import _run_tests
from agent_farm.sandbox import (
    DockerSandboxRunner,
    SandboxError,
    SandboxLimits,
    SandboxManager,
    SandboxUnavailable,
    _validate_read_only_host_command,
    _run_process,
)


class SandboxTests(unittest.TestCase):
    def test_process_runner_closes_pipes_after_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            process = _run_process(
                ["python", "-c", "print('ok')"],
                cwd=Path(tmp),
                environment=dict(os.environ),
                limits=SandboxLimits(10, 512, 1, 32, 4000),
                cancel_check=None,
            )
            self.assertEqual(process[0], 0)
            self.assertEqual(process[1].strip(), "ok")

    def test_windows_read_only_commands_reject_escape_and_process_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            _validate_read_only_host_command(["rg", "needle", "."], root)
            with self.assertRaises(SandboxError):
                _validate_read_only_host_command(["rg", "needle", ".."], root)
            with self.assertRaises(SandboxError):
                _validate_read_only_host_command(["rg", "--pre=evil.exe", "needle"], root)
            with self.assertRaises(SandboxError):
                _validate_read_only_host_command(["git", "diff", "--ext-diff"], root)
            with self.assertRaises(SandboxError):
                _validate_read_only_host_command(["rg", "needle", str(root.parent)], root)

    def test_auto_backend_fails_closed_for_repository_code_without_docker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = SandboxManager(
                backend="auto",
                sandbox_mode="workspace-write",
                memory_mb=512,
                cpus=1,
                pids=64,
                max_output_chars=4000,
            )
            with patch.object(manager.docker, "available", return_value=False):
                with self.assertRaises(SandboxUnavailable):
                    manager.run(
                        ["python", "-m", "pytest"],
                        worktree=root,
                        cwd=root,
                        timeout_seconds=10,
                    )

    def test_auto_backend_runs_read_only_verification_without_docker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "artifact.md").write_text("desktop planning and execution path was verified\n")
            manager = SandboxManager(
                backend="auto",
                sandbox_mode="workspace-write",
                memory_mb=512,
                cpus=1,
                pids=64,
                max_output_chars=4000,
            )
            with patch.object(manager.docker, "available", return_value=False):
                result = manager.run(
                    ["rg", "--fixed-strings", "desktop planning", "artifact.md"],
                    worktree=root,
                    cwd=root,
                    timeout_seconds=10,
                )
            self.assertEqual(result.returncode, 0)
            self.assertIn("desktop planning", result.stdout)
            self.assertEqual(result.manifest["backend"], "windows-restricted")

    def test_docker_runner_uses_copy_network_denial_and_resource_limits(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "test.py").write_text("print('ok')")
            (root / ".env").write_text("SECRET=hidden")
            external = Path(outside) / "credential.txt"
            external.write_text("must not enter sandbox")
            try:
                (root / "external-link.txt").symlink_to(external)
            except OSError:
                self.skipTest("symlinks are unavailable")

            captured: dict[str, object] = {}

            def fake_run(command, **kwargs):
                captured["command"] = command
                mount = command[command.index("--mount") + 1]
                source = Path(mount.split("source=", 1)[1].split(",target=", 1)[0])
                captured["link_copied"] = (source / "external-link.txt").exists()
                captured["source_text"] = (source / "src" / "test.py").read_text()
                captured["secret_copied"] = (source / ".env").exists()
                return 0, "ok", "", False, False, False

            runner = DockerSandboxRunner(forbidden_paths=[".env", "**/.env"])
            runner.binary = "docker"
            runner._availability = True
            with patch("agent_farm.sandbox._run_process", side_effect=fake_run):
                result = runner.run(
                    ["python", "-m", "unittest"],
                    worktree=root,
                    cwd=root,
                    limits=SandboxLimits(30, 768, 1.5, 80, 5000),
                )

            command = captured["command"]
            self.assertIn("--network=none", command)
            self.assertIn("--cap-drop=ALL", command)
            self.assertIn("--read-only", command)
            self.assertEqual(command[command.index("--memory") + 1], "768m")
            self.assertEqual(command[command.index("--cpus") + 1], "1.5")
            self.assertFalse(captured["link_copied"])
            self.assertFalse(captured["secret_copied"])
            self.assertEqual(captured["source_text"], "print('ok')")
            self.assertEqual(result.manifest["network"], "none")
            self.assertEqual(result.manifest["filesystem_roots"], ["/workspace", "/tmp"])

    def test_docker_runner_invalidates_cached_availability_when_daemon_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = DockerSandboxRunner()
            runner.binary = "docker"
            runner._availability = True
            failed = (
                1,
                "",
                "docker: error during connect: Docker Desktop is not running",
                False,
                False,
                False,
            )
            with patch("agent_farm.sandbox._run_process", return_value=failed):
                with self.assertRaisesRegex(SandboxUnavailable, "Docker Desktop"):
                    runner.run(
                        ["python", "-m", "unittest"],
                        worktree=root,
                        cwd=root,
                        limits=SandboxLimits(30, 512, 1, 64, 4000),
                    )
            self.assertFalse(runner._availability)

    def test_machine_test_runner_records_denial_manifest_when_secure_backend_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            worktree = root / "worktree"
            run_dir.mkdir()
            worktree.mkdir()
            paths = RunPaths(root, run_dir, worktree)
            with patch("agent_farm.sandbox.DockerSandboxRunner.available", return_value=False):
                results = _run_tests(
                    paths,
                    ["python -m pytest"],
                    30,
                    AgentFarmConfig(),
                )
            self.assertEqual(results[0].returncode, 126)
            manifest = run_dir / "tests" / "01.capabilities.json"
            self.assertTrue(manifest.is_file())
            self.assertIn('"denied": true', manifest.read_text())


if __name__ == "__main__":
    unittest.main()
