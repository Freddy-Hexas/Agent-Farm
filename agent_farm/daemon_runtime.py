from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from .protocol import PROTOCOL_VERSION


DESCRIPTOR_SCHEMA_VERSION = 1
RUNTIME_PROTOCOL_VERSION = PROTOCOL_VERSION


@dataclass
class DaemonLease:
    """An operating-system lease that permits one runtime per repository."""

    path: Path
    _stream: BinaryIO | None = None

    def acquire(self) -> bool:
        if self._stream is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        try:
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            stream.close()
            return False
        self._stream = stream
        return True

    def close(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is None:
            return
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()

    def __enter__(self) -> "DaemonLease":
        if not self.acquire():
            raise RuntimeError("The Agent Farm daemon lease is already held.")
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()


def read_descriptor(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != DESCRIPTOR_SCHEMA_VERSION:
        return None
    if payload.get("protocol_version") != RUNTIME_PROTOCOL_VERSION:
        return None
    return payload


def write_descriptor(path: Path, payload: dict[str, Any]) -> None:
    descriptor = dict(payload)
    descriptor["schema_version"] = DESCRIPTOR_SCHEMA_VERSION
    descriptor["protocol_version"] = RUNTIME_PROTOCOL_VERSION
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(descriptor, stream, ensure_ascii=True, separators=(",", ":"))
            stream.write("\n")
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def remove_descriptor(path: Path, *, expected_pid: int) -> None:
    descriptor = read_descriptor(path)
    if descriptor is not None and descriptor.get("pid") != expected_pid:
        return
    path.unlink(missing_ok=True)
