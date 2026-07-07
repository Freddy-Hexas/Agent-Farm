from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import AgentFarmConfig

CONFIG_FILE = "agent-farm.config.json"

DEFAULT_FORBIDDEN_PATHS = [
    ".env",
    ".env.*",
    "**/.env",
    "**/.env.*",
    "**/*secret*",
    "**/*credential*",
    "**/*token*",
    ".git/**",
    ".github/workflows/**",
]


def default_config() -> AgentFarmConfig:
    return AgentFarmConfig(forbidden_paths=list(DEFAULT_FORBIDDEN_PATHS))


def default_config_json() -> dict[str, Any]:
    return default_config().to_json()


def load_config(repo_root: Path, explicit_path: Path | None = None) -> AgentFarmConfig:
    config = default_config_json()
    path = explicit_path or repo_root / CONFIG_FILE
    if path.exists():
        loaded = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(loaded, dict):
            raise ValueError(f"Config must be a JSON object: {path}")
        config.update(loaded)
    return AgentFarmConfig.from_dict(config)


def write_default_config(path: Path, *, force: bool = False) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"Config already exists: {path}")
    path.write_text(
        json.dumps(default_config_json(), indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
