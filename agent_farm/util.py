from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .models import CommandResult


def run_command(
    args: list[str] | str,
    cwd: Path,
    *,
    timeout_seconds: int | None = None,
    input_text: str | None = None,
    shell: bool = False,
) -> CommandResult:
    try:
        completed = subprocess.run(
            args,
            cwd=str(cwd),
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            shell=shell,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            args=args,
            cwd=str(cwd),
            returncode=124,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
            timed_out=True,
        )

    return CommandResult(
        args=args,
        cwd=str(cwd),
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def ensure_inside(parent: Path, child: Path) -> None:
    parent_resolved = parent.resolve()
    child_resolved = child.resolve()
    if parent_resolved == child_resolved:
        return
    if parent_resolved not in child_resolved.parents:
        raise ValueError(f"{child_resolved} is outside {parent_resolved}")
