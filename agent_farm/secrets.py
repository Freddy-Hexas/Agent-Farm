from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path


ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _strip_optional_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            raise ValueError(f"Invalid env line {line_number} in {path}: missing '='")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Invalid env line {line_number} in {path}: empty key")
        values[key] = _strip_optional_quotes(value.strip())
    return values


def load_secrets_env(repo_root: Path, secrets_env: str | None) -> dict[str, str]:
    if not secrets_env:
        return {}
    path = Path(secrets_env)
    if not path.is_absolute():
        path = repo_root / path
    if not path.exists():
        return {}
    return parse_env_file(path)


def _resolved_secrets_path(repo_root: Path, secrets_env: str | None) -> Path:
    if not secrets_env:
        raise ValueError("A secrets env path is required before saving API keys.")
    root = repo_root.resolve()
    path = Path(secrets_env)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("The Settings UI can only write API keys inside the repository.") from exc
    return path


def _validate_secret_updates(updates: dict[str, str]) -> None:
    for key, value in updates.items():
        if not ENV_KEY_PATTERN.fullmatch(key):
            raise ValueError(f"Invalid API key environment variable: {key}")
        if not isinstance(value, str) or not value:
            raise ValueError(f"API key for {key} must be a non-empty string.")
        if value != value.strip():
            raise ValueError(f"API key for {key} cannot start or end with whitespace.")
        if any(character in value for character in ("\r", "\n", "\0")):
            raise ValueError(f"API key for {key} contains an unsupported control character.")
        if len(value) > 8192:
            raise ValueError(f"API key for {key} is too long.")


def update_secrets_env(
    repo_root: Path,
    secrets_env: str | None,
    updates: dict[str, str],
) -> Path:
    """Atomically merge API keys into the repository-local secrets env file."""
    _validate_secret_updates(updates)
    path = _resolved_secrets_path(repo_root, secrets_env)
    if not updates:
        return path

    original = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    lines = original.splitlines()
    replaced: set[str] = set()
    rendered: list[str] = []
    for raw_line in lines:
        candidate = raw_line.strip()
        if candidate.startswith("export "):
            candidate = candidate[len("export ") :].strip()
        key = candidate.split("=", 1)[0].strip() if "=" in candidate else ""
        if key in updates:
            rendered.append(f"{key}={updates[key]}")
            replaced.add(key)
        else:
            rendered.append(raw_line)

    if not lines:
        rendered.append("# Managed locally by Agent Farm. This file must not be committed.")
    for key in sorted(set(updates) - replaced):
        rendered.append(f"{key}={updates[key]}")

    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write("\n".join(rendered).rstrip("\n") + "\n")
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return path
