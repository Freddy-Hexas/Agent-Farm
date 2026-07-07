from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import AgentFarmConfig

CONFIG_FILE = "agent-farm.config.json"
LOCAL_CONFIG_FILE = "agent-farm.local.json"
DEFAULT_SECRETS_ENV_FILE = ".agent-farm/secrets.env"

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

CODEX_OVERRIDE_KEYS = {
    "model_auto_compact_token_limit",
    "model_context_window",
    "model_reasoning_effort",
    "model_reasoning_summary",
    "model_supports_reasoning_summaries",
    "model_verbosity",
    "service_tier",
}


def default_config() -> AgentFarmConfig:
    return AgentFarmConfig(forbidden_paths=list(DEFAULT_FORBIDDEN_PATHS))


def default_config_json() -> dict[str, Any]:
    return default_config().to_json()


def _load_json_object(path: Path) -> dict[str, Any]:
    loaded = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(loaded, dict):
        raise ValueError(f"Config must be a JSON object: {path}")
    return loaded


def _normalize_config_data(data: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(data)
    overrides = dict(normalized.get("codex_config_overrides", {}))
    for key in CODEX_OVERRIDE_KEYS:
        if key in normalized:
            overrides[key] = normalized.pop(key)
    if overrides:
        normalized["codex_config_overrides"] = overrides
    return normalized


def load_config(
    repo_root: Path,
    explicit_path: Path | None = None,
    local_path: Path | None = None,
) -> AgentFarmConfig:
    config = default_config_json()
    path = explicit_path or repo_root / CONFIG_FILE
    if path.exists():
        config.update(_load_json_object(path))
    local_config = local_path or repo_root / LOCAL_CONFIG_FILE
    if local_config.exists():
        config.update(_load_json_object(local_config))
    return AgentFarmConfig.from_dict(_normalize_config_data(config))


def write_default_config(path: Path, *, force: bool = False) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"Config already exists: {path}")
    path.write_text(
        json.dumps(default_config_json(), indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def local_config_template() -> dict[str, Any]:
    return {
        "worker_model": "your-worker-model",
        "worker_provider": "my-provider",
        "secrets_env": DEFAULT_SECRETS_ENV_FILE,
        "model_providers": {
            "my-provider": {
                "name": "My Responses-compatible endpoint",
                "base_url": "https://api.example.com/v1",
                "env_key": "AGENT_FARM_WORKER_API_KEY",
                "wire_api": "responses",
            }
        },
    }


def write_local_templates(
    *,
    config_path: Path = Path(LOCAL_CONFIG_FILE),
    secrets_path: Path = Path(DEFAULT_SECRETS_ENV_FILE),
    force: bool = False,
) -> None:
    if config_path.exists() and not force:
        raise FileExistsError(f"Local config already exists: {config_path}")
    if secrets_path.exists() and not force:
        raise FileExistsError(f"Secrets env already exists: {secrets_path}")

    config_path.write_text(
        json.dumps(local_config_template(), indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    secrets_path.write_text(
        "# This file is gitignored. Put real worker API keys here.\n"
        "AGENT_FARM_WORKER_API_KEY=replace-with-your-api-key\n",
        encoding="utf-8",
    )
