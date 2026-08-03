from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from .checkpoints import CheckpointError, CheckpointStore
from .config import config_from_dict
from .git_ops import GitError, apply_patch, git
from .models import RunPaths, TaskStatus
from .orchestrator import _run_tests
from .util import ensure_inside, read_json, write_json


class ChangeControlError(RuntimeError):
    pass


def _worker(result: dict[str, Any], worker_id: str) -> dict[str, Any]:
    worker = next(
        (item for item in result.get("workers", []) if item.get("id") == worker_id),
        None,
    )
    if worker is None:
        raise FileNotFoundError(f"Unknown worker: {worker_id}")
    return worker


def _affected_paths(worker: dict[str, Any]) -> list[str]:
    paths: set[str] = set()
    for item in worker.get("changed_files") or []:
        if not isinstance(item, dict):
            continue
        for key in ("path", "old_path"):
            value = item.get(key)
            if isinstance(value, str) and value:
                paths.add(Path(value).as_posix())
    return sorted(paths)


def build_change_set(
    repo_root: Path,
    result: dict[str, Any],
    worker_id: str,
    *,
    max_bytes: int = 2_000_000,
) -> dict[str, Any]:
    worker = _worker(result, worker_id)
    raw_patch = worker.get("patch_file")
    if not isinstance(raw_patch, str) or not raw_patch:
        raise FileNotFoundError(f"No patch is available for worker: {worker_id}")
    patch_file = Path(raw_patch).resolve()
    ensure_inside(repo_root, patch_file)
    data = patch_file.read_bytes()
    patch = data[:max_bytes].decode("utf-8", errors="replace")
    binary_patch = "GIT binary patch" in patch or "Binary files " in patch
    files = []
    for changed in worker.get("changed_files") or []:
        if not isinstance(changed, dict):
            continue
        files.append(
            {
                "status": changed.get("status"),
                "path": changed.get("path"),
                "old_path": changed.get("old_path"),
                "binary": binary_patch,
            }
        )
    return {
        "schema_version": 1,
        "farm_id": result.get("farm_id"),
        "worker_id": worker_id,
        "role": worker.get("role"),
        "provider": worker.get("provider"),
        "model": worker.get("model"),
        "base_commit": result.get("base_commit"),
        "status": worker.get("status"),
        "files": files,
        "unified_diff": patch,
        "truncated": len(data) > max_bytes,
        "binary": binary_patch,
        "tests": worker.get("tests") or [],
        "machine_review": worker.get("machine_review") or {},
    }


class ChangeController:
    def __init__(
        self,
        repo_root: Path,
        checkpoints: CheckpointStore,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.checkpoints = checkpoints
        self._lock = threading.RLock()

    def change_sets(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        change_sets: list[dict[str, Any]] = []
        for worker in result.get("workers") or []:
            worker_id = worker.get("id") if isinstance(worker, dict) else None
            if not isinstance(worker_id, str):
                continue
            try:
                change_sets.append(build_change_set(self.repo_root, result, worker_id))
            except FileNotFoundError:
                continue
        return change_sets

    def apply(self, farm_dir: Path, worker_id: str) -> dict[str, Any]:
        with self._lock:
            result_file = farm_dir / "result.json"
            result = read_json(result_file)
            decision = result.get("decision") or {}
            if decision.get("decision") != "approve_merge":
                raise ChangeControlError("Supervisor approval is required before applying a patch.")
            if decision.get("approved_worker") != worker_id:
                raise ChangeControlError("Only the Supervisor-approved Worker can be applied.")
            existing = result.get("change_control") or {}
            if existing.get("status") in {"APPLIED", "VERIFIED", "MERGED"}:
                raise ChangeControlError("A candidate is already applied for this Farm.")
            worker = _worker(result, worker_id)
            if (worker.get("machine_review") or {}).get("status") != "passed":
                raise ChangeControlError("The selected Worker did not pass machine review.")
            affected = _affected_paths(worker)
            if not affected:
                raise ChangeControlError("The selected Worker has no file changes to apply.")
            self._reject_workspace_conflicts(affected)
            patch_file = Path(str(worker.get("patch_file"))).resolve()
            ensure_inside(self.repo_root, patch_file)
            try:
                git(self.repo_root, ["apply", "--check", "--binary", str(patch_file)])
            except GitError as exc:
                raise ChangeControlError(f"The candidate patch conflicts with the workspace: {exc}") from exc

            checkpoint = self.checkpoints.create(
                farm_id=str(result["farm_id"]),
                worker_id=worker_id,
                affected_paths=affected,
                patch_file=patch_file,
                base_commit=str(result.get("base_commit") or ""),
            )
            try:
                apply_patch(self.repo_root, patch_file)
                checkpoint = self.checkpoints.mark_applied(checkpoint["checkpoint_id"])
                verification = self._verify(worker, checkpoint)
                if verification["status"] != "passed":
                    self.checkpoints.rollback(checkpoint["checkpoint_id"])
                    result["status"] = "APPLY_FAILED"
                    result["change_control"] = {
                        "status": "ROLLED_BACK",
                        "worker_id": worker_id,
                        "checkpoint_id": checkpoint["checkpoint_id"],
                        "verification": verification,
                    }
                    write_json(result_file, result)
                    raise ChangeControlError(
                        "Applied changes failed verification and were rolled back."
                    )
                checkpoint = self.checkpoints.mark_verified(
                    checkpoint["checkpoint_id"], verification
                )
            except (ChangeControlError, GitError, CheckpointError, OSError, ValueError):
                latest = self.checkpoints.read(checkpoint["checkpoint_id"])
                if latest.get("status") in {"APPLIED", "VERIFIED"}:
                    self.checkpoints.rollback(checkpoint["checkpoint_id"], force=True)
                raise

            result["status"] = "VERIFIED"
            result["change_control"] = {
                "status": "VERIFIED",
                "worker_id": worker_id,
                "checkpoint_id": checkpoint["checkpoint_id"],
                "verification": verification,
            }
            write_json(result_file, result)
            return result

    def merge(self, farm_dir: Path) -> dict[str, Any]:
        with self._lock:
            result_file = farm_dir / "result.json"
            result = read_json(result_file)
            change_control = result.get("change_control") or {}
            if change_control.get("status") != "VERIFIED":
                raise ChangeControlError("A verified applied candidate is required before merge.")
            decision = result.get("decision") or {}
            if (
                decision.get("decision") != "approve_merge"
                or decision.get("approved_worker") != change_control.get("worker_id")
            ):
                raise ChangeControlError("The Supervisor decision no longer matches the candidate.")
            checkpoint = self.checkpoints.mark_merged(change_control["checkpoint_id"])
            change_control["status"] = "MERGED"
            change_control["merged_at"] = checkpoint["updated_at"]
            result["change_control"] = change_control
            result["status"] = TaskStatus.MERGED.value
            write_json(result_file, result)
            return result

    def rollback(self, farm_dir: Path, checkpoint_id: str, *, force: bool = False) -> dict[str, Any]:
        with self._lock:
            result_file = farm_dir / "result.json"
            result = read_json(result_file)
            checkpoint = self.checkpoints.read(checkpoint_id)
            if checkpoint.get("farm_id") != result.get("farm_id"):
                raise ChangeControlError("Checkpoint does not belong to this Farm.")
            checkpoint = self.checkpoints.rollback(checkpoint_id, force=force)
            result["status"] = TaskStatus.ROLLED_BACK.value
            result["change_control"] = {
                **(result.get("change_control") or {}),
                "status": "ROLLED_BACK",
                "checkpoint_id": checkpoint_id,
                "rolled_back_at": checkpoint.get("rolled_back_at"),
            }
            write_json(result_file, result)
            return result

    def _verify(self, worker: dict[str, Any], checkpoint: dict[str, Any]) -> dict[str, Any]:
        affected = [entry["path"] for entry in checkpoint["entries"]]
        try:
            git(self.repo_root, ["diff", "--check", "--", *affected])
            diff_check = {"status": "passed", "command": "git diff --check"}
        except GitError as exc:
            return {
                "status": "failed",
                "diff_check": {"status": "failed", "error": str(exc)},
                "tests": [],
            }

        run_dir = Path(str(worker.get("run_dir") or "")).resolve()
        ensure_inside(self.repo_root, run_dir)
        worker_result = read_json(run_dir / "result.json")
        raw_config = worker_result.get("config")
        if not isinstance(raw_config, dict):
            raise ChangeControlError("Worker verification configuration is missing.")
        config = config_from_dict(raw_config)
        verification_dir = self.checkpoints._dir(checkpoint["checkpoint_id"]) / "verification"
        verification_dir.mkdir(parents=True, exist_ok=True)
        paths = RunPaths(
            repo_root=self.repo_root,
            run_dir=verification_dir,
            worktree=self.repo_root,
        )
        test_results = _run_tests(
            paths,
            config.test_commands,
            config.test_timeout_seconds,
            config,
        )
        tests = [result.to_json() for result in test_results]
        passed = all(result.ok for result in test_results)
        return {
            "status": "passed" if passed else "failed",
            "diff_check": diff_check,
            "tests": tests,
            "test_count": len(tests),
        }

    def _reject_workspace_conflicts(self, affected: list[str]) -> None:
        changed = set(
            part
            for part in git(
                self.repo_root,
                ["diff", "--name-only", "-z", "HEAD", "--"],
            ).stdout.split("\0")
            if part
        )
        changed.update(
            part
            for part in git(
                self.repo_root,
                ["ls-files", "--others", "--exclude-standard", "-z"],
            ).stdout.split("\0")
            if part and not part.startswith(".agent-farm/")
        )
        conflicts = sorted(set(affected) & changed)
        if conflicts:
            raise ChangeControlError(
                "Candidate paths already contain workspace changes: " + ", ".join(conflicts)
            )
