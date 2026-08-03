from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

from .diagnostics import utc_now
from .util import read_json, write_json


class CrashRecoveryReporter:
    def __init__(self, runtime_root: Path) -> None:
        self.runtime_root = runtime_root.resolve()
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.session_path = self.runtime_root / "active-session.json"
        self.report_path = self.runtime_root / "last-recovery.json"
        self.session_id = uuid.uuid4().hex
        self._report: dict[str, Any] | None = None

    def start(self) -> dict[str, Any] | None:
        previous: dict[str, Any] | None = None
        if self.session_path.is_file():
            try:
                loaded = read_json(self.session_path)
                previous = loaded if isinstance(loaded, dict) else None
            except (OSError, ValueError):
                previous = {"session_id": "unreadable", "started_at": None}
        if previous is not None:
            self._report = {
                "schema_version": 1,
                "detected": True,
                "detected_at": utc_now(),
                "previous_session_id": previous.get("session_id"),
                "previous_started_at": previous.get("started_at"),
                "previous_pid": previous.get("pid"),
                "interrupted_jobs": 0,
                "message": "Agent Farm recovered after the previous runtime ended unexpectedly.",
            }
            write_json(self.report_path, self._report)
        write_json(
            self.session_path,
            {
                "schema_version": 1,
                "session_id": self.session_id,
                "pid": os.getpid(),
                "started_at": utc_now(),
            },
        )
        return self.report

    @property
    def report(self) -> dict[str, Any] | None:
        return dict(self._report) if self._report else None

    def record_reconciliation(self, interrupted_jobs: int) -> None:
        if self._report is None:
            return
        self._report["interrupted_jobs"] = max(0, int(interrupted_jobs))
        write_json(self.report_path, self._report)

    def mark_clean_shutdown(self) -> None:
        try:
            current = read_json(self.session_path)
        except (OSError, ValueError):
            current = None
        if isinstance(current, dict) and current.get("session_id") == self.session_id:
            self.session_path.unlink(missing_ok=True)
