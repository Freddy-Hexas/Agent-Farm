from __future__ import annotations

import json
import os
import platform
import re
import sqlite3
import sys
import tempfile
import threading
import uuid
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__


MAX_EXPORTED_LOG_BYTES = 2_000_000
_SENSITIVE_KEYS = ("api_key", "authorization", "credential", "password", "secret", "token")
_BEARER_PATTERN = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{8,}")
_KEY_PATTERN = re.compile(r"(?i)\b(sk|key|token)[-_][A-Za-z0-9._-]{8,}")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def valid_correlation_id(value: str | None) -> str:
    if value and 8 <= len(value) <= 128 and all(char.isalnum() or char in "._-" for char in value):
        return value
    return new_correlation_id()


def redact(value: Any, *, key: str = "") -> Any:
    if any(part in key.casefold() for part in _SENSITIVE_KEYS):
        return "***REDACTED***"
    if isinstance(value, dict):
        return {str(item_key): redact(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _KEY_PATTERN.sub("***REDACTED***", _BEARER_PATTERN.sub(r"\1***REDACTED***", value))
    return value


class StructuredLogger:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def log(
        self,
        event: str,
        *,
        level: str = "INFO",
        correlation_id: str | None = None,
        **fields: Any,
    ) -> None:
        payload = {
            "timestamp": utc_now(),
            "level": level.upper(),
            "event": event,
            "correlation_id": correlation_id,
            "pid": os.getpid(),
            **fields,
        }
        encoded = json.dumps(redact(payload), ensure_ascii=True, separators=(",", ":"))
        with self._lock:
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(encoded + "\n")


def _read_log_tail(path: Path) -> str:
    if not path.is_file():
        return ""
    with path.open("rb") as stream:
        size = stream.seek(0, os.SEEK_END)
        stream.seek(max(0, size - MAX_EXPORTED_LOG_BYTES))
        raw = stream.read(MAX_EXPORTED_LOG_BYTES)
    return str(redact(raw.decode("utf-8", errors="replace")))


def _runtime_summary(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"available": False}
    try:
        with closing(sqlite3.connect(path)) as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            rows = connection.execute(
                "SELECT kind, status, COUNT(*) FROM runtime_jobs GROUP BY kind, status"
            ).fetchall()
            event_count = int(connection.execute("SELECT COUNT(*) FROM runtime_events").fetchone()[0])
            session_table = int(
                connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'runtime_sessions'"
                ).fetchone()[0]
            )
            session_count = (
                int(connection.execute("SELECT COUNT(*) FROM runtime_sessions").fetchone()[0])
                if session_table
                else 0
            )
            session_event_count = (
                int(connection.execute("SELECT COUNT(*) FROM session_events").fetchone()[0])
                if session_table
                else 0
            )
        return {
            "available": True,
            "schema_version": version,
            "jobs": [
                {"kind": str(kind), "status": str(status), "count": int(count)}
                for kind, status, count in rows
            ],
            "event_count": event_count,
            "session_count": session_count,
            "session_event_count": session_event_count,
        }
    except (OSError, sqlite3.Error) as exc:
        return {"available": False, "error": str(exc)}


def create_diagnostic_bundle(
    repo_root: Path,
    *,
    sanitized_config: dict[str, Any],
    recovery_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    runtime_root = repo_root / ".agent-farm"
    output_root = runtime_root / "diagnostics"
    output_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bundle_path = output_root / f"agent-farm-diagnostics-{stamp}-{uuid.uuid4().hex[:6]}.zip"

    with tempfile.TemporaryDirectory(prefix="agent-farm-diagnostics-") as temporary:
        staging = Path(temporary)
        manifest = {
            "schema_version": 1,
            "created_at": utc_now(),
            "app_version": __version__,
            "python_version": sys.version,
            "platform": platform.platform(),
            "repository_name": repo_root.name,
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
        )
        (staging / "config.sanitized.json").write_text(
            json.dumps(redact(sanitized_config), indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        (staging / "runtime-summary.json").write_text(
            json.dumps(_runtime_summary(runtime_root / "runtime.sqlite3"), indent=2) + "\n",
            encoding="utf-8",
        )
        if recovery_report:
            (staging / "recovery-report.json").write_text(
                json.dumps(redact(recovery_report), indent=2, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )
        for source, target in (
            (runtime_root / "logs" / "runtime.log", "runtime.log"),
            (runtime_root / "logs" / "events.jsonl", "events.jsonl"),
        ):
            content = _read_log_tail(source)
            if content:
                (staging / target).write_text(content, encoding="utf-8")
        with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(staging.iterdir()):
                archive.write(path, arcname=path.name)

    return {
        "schema_version": 1,
        "path": str(bundle_path),
        "size_bytes": bundle_path.stat().st_size,
        "created_at": utc_now(),
    }
