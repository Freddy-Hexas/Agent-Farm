from __future__ import annotations

import hashlib
import os
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .util import ensure_inside, read_json, write_json


class CheckpointError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _file_state(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        return {
            "exists": True,
            "kind": "symlink",
            "link_target": os.readlink(path),
            "sha256": hashlib.sha256(os.readlink(path).encode("utf-8")).hexdigest(),
        }
    if not path.exists():
        return {"exists": False, "kind": "absent", "sha256": None}
    if not path.is_file():
        raise CheckpointError(f"Checkpoint paths must be files: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "exists": True,
        "kind": "file",
        "size": path.stat().st_size,
        "sha256": digest.hexdigest(),
    }


class CheckpointStore:
    """File-level reversible checkpoints for candidate patch application."""

    def __init__(self, repo_root: Path, root: Path, *, retention: int = 25) -> None:
        self.repo_root = repo_root.resolve()
        self.root = root.resolve()
        ensure_inside(self.repo_root, self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.retention = max(1, retention)
        self._lock = threading.RLock()

    def _dir(self, checkpoint_id: str) -> Path:
        if not checkpoint_id.startswith("checkpoint-") or any(
            char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in checkpoint_id
        ):
            raise ValueError("Invalid checkpoint id.")
        path = (self.root / checkpoint_id).resolve()
        ensure_inside(self.root, path)
        return path

    def _repo_path(self, relative: str | Path) -> Path:
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise CheckpointError(f"Invalid checkpoint path: {relative}")
        path = self.repo_root / candidate
        ensure_inside(self.repo_root, path.parent.resolve())
        return path

    def create(
        self,
        *,
        farm_id: str,
        worker_id: str,
        affected_paths: list[str],
        patch_file: Path,
        base_commit: str,
    ) -> dict[str, Any]:
        normalized = sorted({Path(path).as_posix().strip("/") for path in affected_paths if path})
        if not normalized:
            raise CheckpointError("A checkpoint requires at least one affected path.")
        checkpoint_id = "checkpoint-" + uuid.uuid4().hex[:20]
        directory = self._dir(checkpoint_id)
        before_root = directory / "before"
        with self._lock:
            directory.mkdir(parents=True, exist_ok=False)
            entries: list[dict[str, Any]] = []
            for relative in normalized:
                candidate = Path(relative)
                source = self._repo_path(candidate)
                state = _file_state(source)
                entry = {"path": relative, "before": state, "after": None}
                if state["kind"] == "file":
                    destination = before_root / candidate
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
                entries.append(entry)
            metadata = {
                "schema_version": 1,
                "checkpoint_id": checkpoint_id,
                "farm_id": farm_id,
                "worker_id": worker_id,
                "status": "CREATED",
                "base_commit": base_commit,
                "patch_file": str(patch_file.resolve()),
                "created_at": _utc_now(),
                "updated_at": _utc_now(),
                "entries": entries,
                "verification": None,
            }
            write_json(directory / "checkpoint.json", metadata)
            self._prune_unlocked()
            return metadata

    def read(self, checkpoint_id: str) -> dict[str, Any]:
        path = self._dir(checkpoint_id) / "checkpoint.json"
        if not path.is_file():
            raise FileNotFoundError(f"Unknown checkpoint: {checkpoint_id}")
        return read_json(path)

    def list(self, *, farm_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            checkpoints: list[dict[str, Any]] = []
            for path in self.root.glob("checkpoint-*/checkpoint.json"):
                try:
                    checkpoint = read_json(path)
                except (OSError, ValueError):
                    continue
                if farm_id is None or checkpoint.get("farm_id") == farm_id:
                    checkpoints.append(checkpoint)
            return sorted(
                checkpoints,
                key=lambda item: str(item.get("created_at") or ""),
                reverse=True,
            )

    def mark_applied(self, checkpoint_id: str) -> dict[str, Any]:
        with self._lock:
            checkpoint = self.read(checkpoint_id)
            for entry in checkpoint["entries"]:
                path = self._repo_path(entry["path"])
                if path.is_symlink():
                    target = path.resolve()
                    ensure_inside(self.repo_root, target)
                entry["after"] = _file_state(path)
            checkpoint["status"] = "APPLIED"
            checkpoint["updated_at"] = _utc_now()
            self._write(checkpoint)
            return checkpoint

    def mark_verified(
        self,
        checkpoint_id: str,
        verification: dict[str, Any],
    ) -> dict[str, Any]:
        with self._lock:
            checkpoint = self.read(checkpoint_id)
            checkpoint["status"] = "VERIFIED"
            checkpoint["verification"] = verification
            checkpoint["updated_at"] = _utc_now()
            self._write(checkpoint)
            return checkpoint

    def mark_merged(self, checkpoint_id: str) -> dict[str, Any]:
        with self._lock:
            checkpoint = self.read(checkpoint_id)
            if checkpoint.get("status") != "VERIFIED":
                raise CheckpointError("Only a verified checkpoint can be merged.")
            self._assert_unchanged(checkpoint, state_key="after")
            checkpoint["status"] = "MERGED"
            checkpoint["updated_at"] = _utc_now()
            self._write(checkpoint)
            return checkpoint

    def rollback(self, checkpoint_id: str, *, force: bool = False) -> dict[str, Any]:
        with self._lock:
            checkpoint = self.read(checkpoint_id)
            if checkpoint.get("status") == "ROLLED_BACK":
                return checkpoint
            if checkpoint.get("status") not in {"APPLIED", "VERIFIED", "MERGED"}:
                raise CheckpointError("The checkpoint has not been applied.")
            if not force:
                self._assert_unchanged(checkpoint, state_key="after")
            before_root = self._dir(checkpoint_id) / "before"
            for entry in checkpoint["entries"]:
                relative = Path(entry["path"])
                destination = self._repo_path(relative)
                state = entry["before"]
                if destination.is_symlink() or destination.is_file():
                    destination.unlink()
                elif destination.exists():
                    raise CheckpointError(f"Rollback target became a directory: {entry['path']}")
                if state["kind"] == "file":
                    source = before_root / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
                elif state["kind"] == "symlink":
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.symlink_to(state["link_target"])
            checkpoint["status"] = "ROLLED_BACK"
            checkpoint["rolled_back_at"] = _utc_now()
            checkpoint["updated_at"] = checkpoint["rolled_back_at"]
            self._write(checkpoint)
            return checkpoint

    def _assert_unchanged(self, checkpoint: dict[str, Any], *, state_key: str) -> None:
        conflicts: list[str] = []
        for entry in checkpoint["entries"]:
            expected = entry.get(state_key)
            if expected is None:
                continue
            path = self._repo_path(entry["path"])
            if _file_state(path) != expected:
                conflicts.append(entry["path"])
        if conflicts:
            raise CheckpointError(
                "Files changed after checkpoint application: " + ", ".join(conflicts)
            )

    def _write(self, checkpoint: dict[str, Any]) -> None:
        write_json(
            self._dir(str(checkpoint["checkpoint_id"])) / "checkpoint.json",
            checkpoint,
        )

    def _prune_unlocked(self) -> None:
        checkpoints = self.list()
        removable = [
            checkpoint
            for checkpoint in checkpoints[self.retention :]
            if checkpoint.get("status") in {"CREATED", "ROLLED_BACK"}
        ]
        for checkpoint in removable:
            shutil.rmtree(self._dir(str(checkpoint["checkpoint_id"])), ignore_errors=True)
