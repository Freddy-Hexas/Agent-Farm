from __future__ import annotations

import hashlib
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class ArtifactRetentionManager:
    def __init__(
        self,
        runtime_root: Path,
        *,
        retention_days: int = 30,
        max_backups: int = 5,
        max_diagnostics: int = 10,
    ) -> None:
        self.runtime_root = runtime_root.resolve()
        self.retention_days = retention_days
        self.max_backups = max_backups
        self.max_diagnostics = max_diagnostics
        self.backup_root = self.runtime_root / "backups"

    def maintain(self, *, config_path: Path | None = None) -> dict[str, Any]:
        self.backup_root.mkdir(parents=True, exist_ok=True)
        created: list[str] = []
        runtime_database = self.runtime_root / "runtime.sqlite3"
        if runtime_database.is_file():
            backup = self._backup_sqlite(runtime_database)
            if backup is not None:
                created.append(str(backup))
        if config_path is not None and config_path.is_file():
            backup = self._backup_file(config_path, "config")
            if backup is not None:
                created.append(str(backup))
        removed = 0
        removed += self._cleanup(self.backup_root, "runtime-*.sqlite3", self.max_backups)
        removed += self._cleanup(self.backup_root, "config-*.json", self.max_backups)
        removed += self._cleanup(
            self.runtime_root / "diagnostics", "*.zip", self.max_diagnostics
        )
        removed += self._cleanup(
            self.runtime_root / "ui-submissions", "*.json", 200
        )
        return {"created": created, "removed": removed}

    def _backup_sqlite(self, source: Path) -> Path | None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = self.backup_root / f"runtime-{stamp}.sqlite3"
        with closing(sqlite3.connect(source)) as source_connection, closing(
            sqlite3.connect(target)
        ) as target_connection:
            source_connection.backup(target_connection)
        return self._deduplicate(target, "runtime-*.sqlite3")

    def _backup_file(self, source: Path, prefix: str) -> Path | None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = self.backup_root / f"{prefix}-{stamp}{source.suffix}"
        shutil.copy2(source, target)
        return self._deduplicate(target, f"{prefix}-*{source.suffix}")

    def _deduplicate(self, target: Path, pattern: str) -> Path | None:
        digest = hashlib.sha256(target.read_bytes()).digest()
        previous = [
            path
            for path in sorted(self.backup_root.glob(pattern), key=lambda item: item.stat().st_mtime, reverse=True)
            if path != target
        ]
        if previous and hashlib.sha256(previous[0].read_bytes()).digest() == digest:
            target.unlink(missing_ok=True)
            return None
        return target

    def _cleanup(self, root: Path, pattern: str, keep: int) -> int:
        if not root.is_dir():
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        files = sorted(
            (path for path in root.glob(pattern) if path.is_file()),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        removed = 0
        for index, path in enumerate(files):
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if index >= keep or modified < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        return removed
