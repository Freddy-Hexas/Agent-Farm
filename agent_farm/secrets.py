from __future__ import annotations

from pathlib import Path


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
