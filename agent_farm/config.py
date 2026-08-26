from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .harnesses import HARNESS_IDS
from .models import AgentFarmConfig

CONFIG_FILE = "agent-farm.config.json"
LOCAL_CONFIG_FILE = "agent-farm.local.json"
DEFAULT_SECRETS_ENV_FILE = ".agent-farm/secrets.env"
CONFIG_SCHEMA_VERSION = 2
CONFIG_SCHEMA_KEY = "_schema_version"

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

MODEL_PROVIDER_FIELDS = {
    "template_id",
    "name",
    "base_url",
    "env_key",
    "wire_api",
    "http_headers",
    "env_http_headers",
    "extra_query",
    "request_max_retries",
    "requires_openai_auth",
    "stream_idle_timeout_ms",
    "stream_max_retries",
    "supports_websockets",
}

SANDBOX_MODES = {"read-only", "workspace-write", "danger-full-access"}
NATIVE_SANDBOX_BACKENDS = {"auto", "windows", "docker"}
APPROVAL_POLICIES = {"untrusted", "on-failure", "on-request", "never"}
BUDGET_POLICIES = {"warn", "hard-stop"}
AGENT_BACKENDS = {"native", "codex"}
REASONING_MODES = {"enabled", "disabled"}
REASONING_EFFORTS = {"none", "default", "minimal", "low", "medium", "high", "xhigh", "max"}
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

PROFILE_KEY_MAP = {
    "harness": "worker_harness",
    "model": "worker_model",
    "provider": "worker_provider",
    "reasoning_mode": "worker_reasoning_mode",
    "reasoning_effort": "worker_reasoning_effort",
    "oss": "worker_oss",
    "local_provider": "worker_local_provider",
    "codex_profile": "worker_codex_profile",
    "codex_profile_v2": "worker_codex_profile_v2",
    "secrets_env": "secrets_env",
    "timeout_seconds": "timeout_seconds",
    "budget_usd": "worker_budget_usd",
    "sandbox": "sandbox",
    "approval_policy": "approval_policy",
    "ephemeral": "ephemeral",
    "codex_json": "codex_json",
    "codex_config_overrides": "codex_config_overrides",
}

LEGACY_ROOT_KEY_MAP = {
    "model": "worker_model",
    "provider": "worker_provider",
    "reasoning_mode": "worker_reasoning_mode",
    "reasoning_effort": "worker_reasoning_effort",
    "oss": "worker_oss",
    "local_provider": "worker_local_provider",
}

# Metadata is persisted with a route but is never copied onto the resolved
# AgentFarmConfig used to launch a Worker. This keeps stable routing IDs such
# as "cheap" separate from the human-facing name shown in the desktop app.
WORKER_PROFILE_METADATA_FIELDS = {
    "display_name",
    "capability_tier",
    "fallback_profiles",
    "escalation_profile",
}
WORKER_PROFILE_FIELDS = set(PROFILE_KEY_MAP) | WORKER_PROFILE_METADATA_FIELDS


def default_config() -> AgentFarmConfig:
    return AgentFarmConfig(forbidden_paths=list(DEFAULT_FORBIDDEN_PATHS))


def default_config_json() -> dict[str, Any]:
    return default_config().to_json()


def _load_json_object(path: Path) -> dict[str, Any]:
    loaded = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(loaded, dict):
        raise ValueError(f"Config must be a JSON object: {path}")
    return _normalize_config_data(loaded)


def _normalize_config_data(data: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(data)
    version = normalized.pop(CONFIG_SCHEMA_KEY, 1)
    if type(version) is not int or version < 1:
        raise ValueError(f"{CONFIG_SCHEMA_KEY} must be a positive integer.")
    if version > CONFIG_SCHEMA_VERSION:
        raise ValueError(
            f"Config schema {version} is newer than supported schema {CONFIG_SCHEMA_VERSION}."
        )
    if version == 1:
        for legacy, current in LEGACY_ROOT_KEY_MAP.items():
            if legacy in normalized and current not in normalized:
                normalized[current] = normalized.pop(legacy)
    overrides = dict(normalized.get("codex_config_overrides", {}))
    for key in CODEX_OVERRIDE_KEYS:
        if key in normalized:
            overrides[key] = normalized.pop(key)
    if overrides:
        normalized["codex_config_overrides"] = overrides
    # Persist the new role-specific names when an older config explicitly
    # selected a backend. Configs without the legacy key keep the dataclass
    # fallback so partial local overlays remain valid.
    if "agent_backend" in normalized:
        normalized.setdefault("supervisor_harness", normalized["agent_backend"])
        normalized.setdefault("worker_harness", normalized["agent_backend"])
    return normalized


def _config_document(config: AgentFarmConfig) -> dict[str, Any]:
    return {CONFIG_SCHEMA_KEY: CONFIG_SCHEMA_VERSION, **config.to_json()}


def _optional_string(value: Any, label: str, *, max_length: int = 2048) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string or null.")
    if len(value) > max_length:
        raise ValueError(f"{label} is too long.")


def _positive_integer(value: Any, label: str, *, maximum: int) -> None:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{label} must be between 1 and {maximum}.")


def _optional_positive_number(value: Any, label: str) -> None:
    if value is None:
        return
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{label} must be a positive number or null.")


def _string_list(value: Any, label: str) -> None:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a list of strings.")


def _validate_provider(provider_id: str, provider: Any) -> None:
    if not IDENTIFIER_PATTERN.fullmatch(provider_id):
        raise ValueError(f"Invalid model provider id: {provider_id}")
    if not isinstance(provider, dict):
        raise ValueError(f"Model provider must be an object: {provider_id}")
    unknown = sorted(set(provider) - MODEL_PROVIDER_FIELDS)
    if unknown:
        raise ValueError(
            f"Unsupported model provider fields for {provider_id}: {', '.join(unknown)}"
        )
    for key in ("template_id", "name", "base_url", "env_key", "wire_api"):
        _optional_string(provider.get(key), f"model_providers.{provider_id}.{key}")
    env_key = provider.get("env_key")
    if env_key and not ENV_KEY_PATTERN.fullmatch(env_key):
        raise ValueError(f"Invalid environment variable name for provider {provider_id}.")
    base_url = provider.get("base_url")
    if base_url and not base_url.startswith(("http://", "https://")):
        raise ValueError(f"Provider base_url must use http or https: {provider_id}")
    for key in ("http_headers", "env_http_headers", "extra_query"):
        value = provider.get(key)
        if value is not None and not isinstance(value, dict):
            raise ValueError(f"model_providers.{provider_id}.{key} must be an object.")
    for key in ("request_max_retries", "stream_idle_timeout_ms", "stream_max_retries"):
        value = provider.get(key)
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(f"model_providers.{provider_id}.{key} must be a non-negative integer.")
    for key in ("requires_openai_auth", "supports_websockets"):
        value = provider.get(key)
        if value is not None and type(value) is not bool:
            raise ValueError(f"model_providers.{provider_id}.{key} must be a boolean.")


def _validate_price_overrides(overrides: Any) -> None:
    if not isinstance(overrides, dict):
        raise ValueError("model_price_overrides must be a JSON object.")
    allowed = {"input", "cached_input", "cache_write_input", "output", "source"}
    for route, raw in overrides.items():
        if not isinstance(route, str) or "/" not in route or len(route) > 256:
            raise ValueError("Model price override keys must use provider/model patterns.")
        if not isinstance(raw, dict):
            raise ValueError(f"Model price override must be an object: {route}")
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise ValueError(f"Unsupported price fields for {route}: {', '.join(unknown)}")
        for required in ("input", "output"):
            if required not in raw:
                raise ValueError(f"Model price override {route} requires {required}.")
        for key in allowed - {"source"}:
            if key not in raw:
                continue
            value = raw[key]
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                raise ValueError(f"Model price override {route}.{key} must be non-negative.")
        _optional_string(raw.get("source"), f"model_price_overrides.{route}.source")


def _validate_worker_profile(name: str, profile: Any) -> None:
    if not IDENTIFIER_PATTERN.fullmatch(name):
        raise ValueError(f"Invalid worker profile name: {name}")
    if not isinstance(profile, dict):
        raise ValueError(f"Worker profile must be an object: {name}")
    unknown = sorted(set(profile) - WORKER_PROFILE_FIELDS)
    if unknown:
        raise ValueError(f"Unknown keys in worker profile '{name}': {', '.join(unknown)}")
    for key in (
        "harness",
        "model",
        "provider",
        "reasoning_mode",
        "reasoning_effort",
        "local_provider",
        "codex_profile",
        "codex_profile_v2",
        "secrets_env",
    ):
        _optional_string(profile.get(key), f"worker_profiles.{name}.{key}")
    _optional_string(
        profile.get("display_name"),
        f"worker_profiles.{name}.display_name",
        max_length=120,
    )
    harness = profile.get("harness")
    if harness is not None and harness not in HARNESS_IDS:
        raise ValueError(f"worker_profiles.{name}.harness must be native or codex.")
    capability_tier = profile.get("capability_tier", "standard")
    if capability_tier not in {"economy", "standard", "premium"}:
        raise ValueError(
            f"worker_profiles.{name}.capability_tier must be economy, standard, or premium."
        )
    fallback_profiles = profile.get("fallback_profiles")
    if fallback_profiles is not None:
        _string_list(fallback_profiles, f"worker_profiles.{name}.fallback_profiles")
    _optional_string(
        profile.get("escalation_profile"),
        f"worker_profiles.{name}.escalation_profile",
    )
    # A provider may be declared in the user's Codex config instead of Agent Farm.
    timeout = profile.get("timeout_seconds")
    if timeout is not None:
        _positive_integer(timeout, f"worker_profiles.{name}.timeout_seconds", maximum=86_400)
    _optional_positive_number(profile.get("budget_usd"), f"worker_profiles.{name}.budget_usd")
    sandbox = profile.get("sandbox")
    if sandbox is not None and sandbox not in SANDBOX_MODES:
        raise ValueError(f"Invalid sandbox mode in worker profile '{name}'.")
    approval = profile.get("approval_policy")
    if approval is not None and approval not in APPROVAL_POLICIES:
        raise ValueError(f"Invalid approval policy in worker profile '{name}'.")
    reasoning_mode = profile.get("reasoning_mode")
    if reasoning_mode is not None and reasoning_mode not in REASONING_MODES:
        raise ValueError(f"Invalid reasoning mode in worker profile '{name}'.")
    reasoning_effort = profile.get("reasoning_effort")
    if reasoning_effort is not None and reasoning_effort not in REASONING_EFFORTS:
        raise ValueError(f"Invalid reasoning effort in worker profile '{name}'.")
    for key in ("oss", "ephemeral", "codex_json"):
        value = profile.get(key)
        if value is not None and type(value) is not bool:
            raise ValueError(f"worker_profiles.{name}.{key} must be a boolean.")
    overrides = profile.get("codex_config_overrides")
    if overrides is not None and not isinstance(overrides, dict):
        raise ValueError(f"worker_profiles.{name}.codex_config_overrides must be an object.")


def validate_config(config: AgentFarmConfig) -> AgentFarmConfig:
    """Validate runtime and UI-edited configuration without resolving secrets."""
    for key in (
        "agent_backend",
        "supervisor_harness",
        "worker_harness",
        "codex_binary",
        "supervisor_model",
        "supervisor_provider",
        "supervisor_reasoning_mode",
        "supervisor_reasoning_effort",
        "supervisor_codex_profile",
        "worker_model",
        "worker_provider",
        "worker_reasoning_mode",
        "worker_reasoning_effort",
        "worker_local_provider",
        "worker_codex_profile",
        "worker_codex_profile_v2",
        "secrets_env",
        "runs_dir",
        "farms_dir",
        "worktrees_dir",
    ):
        _optional_string(getattr(config, key), key)
    if config.agent_backend not in AGENT_BACKENDS:
        raise ValueError("agent_backend must be native or codex.")
    for key in ("supervisor_harness", "worker_harness"):
        value = getattr(config, key)
        if value is not None and value not in HARNESS_IDS:
            raise ValueError(f"{key} must be native or codex.")
    if config.agent_backend == "codex" and not config.codex_binary.strip():
        raise ValueError("codex_binary must not be empty.")
    for key in ("supervisor_reasoning_mode", "worker_reasoning_mode"):
        value = getattr(config, key)
        if value is not None and value not in REASONING_MODES:
            raise ValueError(f"{key} must be enabled, disabled, or null.")
    for key in ("supervisor_reasoning_effort", "worker_reasoning_effort"):
        value = getattr(config, key)
        if value is not None and value not in REASONING_EFFORTS:
            raise ValueError(f"{key} has an unsupported value.")
    for key in ("runs_dir", "farms_dir", "worktrees_dir"):
        raw_path = getattr(config, key).strip()
        if not raw_path:
            raise ValueError(f"{key} must not be empty.")
        path = Path(raw_path)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"{key} must stay inside the repository.")
    _positive_integer(config.supervisor_timeout_seconds, "supervisor_timeout_seconds", maximum=86_400)
    _positive_integer(config.max_parallel_workers, "max_parallel_workers", maximum=12)
    _positive_integer(config.timeout_seconds, "timeout_seconds", maximum=86_400)
    _positive_integer(config.native_max_turns, "native_max_turns", maximum=200)
    _positive_integer(
        config.native_command_timeout_seconds,
        "native_command_timeout_seconds",
        maximum=86_400,
    )
    _positive_integer(
        config.native_max_output_chars,
        "native_max_output_chars",
        maximum=2_000_000,
    )
    if config.native_sandbox_backend not in NATIVE_SANDBOX_BACKENDS:
        raise ValueError("native_sandbox_backend must be auto, windows, or docker.")
    _positive_integer(
        config.native_sandbox_memory_mb,
        "native_sandbox_memory_mb",
        maximum=131_072,
    )
    if not isinstance(config.native_sandbox_cpus, (int, float)) or not 0.1 <= float(
        config.native_sandbox_cpus
    ) <= 128:
        raise ValueError("native_sandbox_cpus must be between 0.1 and 128.")
    _positive_integer(config.native_sandbox_pids, "native_sandbox_pids", maximum=32_768)
    _positive_integer(
        config.provider_failure_threshold,
        "provider_failure_threshold",
        maximum=100,
    )
    _positive_integer(
        config.provider_cooldown_seconds,
        "provider_cooldown_seconds",
        maximum=86_400,
    )
    if type(config.max_worker_escalations) is not int or not 0 <= config.max_worker_escalations <= 5:
        raise ValueError("max_worker_escalations must be between 0 and 5.")
    _positive_integer(config.test_timeout_seconds, "test_timeout_seconds", maximum=86_400)
    _positive_integer(config.artifact_retention_days, "artifact_retention_days", maximum=3_650)
    _positive_integer(config.max_runtime_backups, "max_runtime_backups", maximum=100)
    _positive_integer(config.max_diagnostic_bundles, "max_diagnostic_bundles", maximum=100)
    _positive_integer(config.max_diff_lines, "max_diff_lines", maximum=1_000_000)
    _positive_integer(config.max_changed_files, "max_changed_files", maximum=100_000)
    if config.sandbox not in SANDBOX_MODES:
        raise ValueError("sandbox must be read-only, workspace-write, or danger-full-access.")
    if config.approval_policy not in APPROVAL_POLICIES:
        raise ValueError("approval_policy must be untrusted, on-failure, on-request, or never.")
    if config.budget_policy not in BUDGET_POLICIES:
        raise ValueError("budget_policy must be warn or hard-stop.")
    for key in ("worker_budget_usd", "farm_budget_usd", "monthly_budget_usd"):
        _optional_positive_number(getattr(config, key), key)
    if not isinstance(config.budget_warning_ratio, (int, float)) or isinstance(
        config.budget_warning_ratio, bool
    ) or not 0 < float(config.budget_warning_ratio) <= 1:
        raise ValueError("budget_warning_ratio must be greater than 0 and at most 1.")
    for key in (
        "worker_oss",
        "auto_supervisor_review",
        "allow_lockfiles",
        "codex_json",
        "ephemeral",
    ):
        if type(getattr(config, key)) is not bool:
            raise ValueError(f"{key} must be a boolean.")
    for key in ("allowed_paths", "forbidden_paths", "test_commands"):
        _string_list(getattr(config, key), key)
    if not isinstance(config.model_providers, dict):
        raise ValueError("model_providers must be a JSON object.")
    for provider_id, provider in config.model_providers.items():
        if not isinstance(provider_id, str):
            raise ValueError("Model provider ids must be strings.")
        _validate_provider(provider_id, provider)
    _validate_price_overrides(config.model_price_overrides)
    if not isinstance(config.worker_profiles, dict):
        raise ValueError("worker_profiles must be a JSON object.")
    for name, profile in config.worker_profiles.items():
        if not isinstance(name, str):
            raise ValueError("Worker profile names must be strings.")
        _validate_worker_profile(name, profile)
    for name, profile in config.worker_profiles.items():
        if not isinstance(profile, dict):
            continue
        references = list(profile.get("fallback_profiles") or [])
        if profile.get("escalation_profile"):
            references.append(profile["escalation_profile"])
        unknown_profiles = sorted(set(references) - set(config.worker_profiles))
        if unknown_profiles:
            raise ValueError(
                f"Worker profile '{name}' references unknown fallback profiles: "
                + ", ".join(unknown_profiles)
            )
    if config.default_worker_profile is not None:
        if not isinstance(config.default_worker_profile, str):
            raise ValueError("default_worker_profile must be a string or null.")
        if config.default_worker_profile not in config.worker_profiles:
            raise ValueError("default_worker_profile must reference an existing worker profile.")
    if not isinstance(config.codex_config_overrides, dict):
        raise ValueError("codex_config_overrides must be a JSON object.")
    return config


def config_from_dict(data: dict[str, Any]) -> AgentFarmConfig:
    if not isinstance(data, dict):
        raise ValueError("Config must be a JSON object.")
    return validate_config(AgentFarmConfig.from_dict(_normalize_config_data(data)))


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
    return config_from_dict(config)


def resolve_worker_profile(
    config: AgentFarmConfig,
    profile_name: str | None,
) -> tuple[AgentFarmConfig, str | None]:
    selected = profile_name or config.default_worker_profile
    if not selected:
        return config, None

    # Older local configurations stored one Worker route directly on the root
    # config. Keep that route usable while the Settings UI migrates it.
    if selected == "default" and not config.worker_profiles:
        return config, "default"

    if not isinstance(config.worker_profiles, dict):
        raise ValueError("worker_profiles must be a JSON object")
    profile = config.worker_profiles.get(selected)
    if profile is None:
        available = ", ".join(sorted(config.worker_profiles)) or "none"
        raise ValueError(f"Unknown worker profile '{selected}'. Available profiles: {available}")
    if not isinstance(profile, dict):
        raise ValueError(f"Worker profile must be a JSON object: {selected}")

    unknown = sorted(set(profile) - WORKER_PROFILE_FIELDS)
    if unknown:
        raise ValueError(
            f"Unknown keys in worker profile '{selected}': {', '.join(unknown)}"
        )

    data = config.to_json()
    for source_key, value in profile.items():
        if source_key in WORKER_PROFILE_METADATA_FIELDS:
            continue
        target_key = PROFILE_KEY_MAP[source_key]
        if target_key == "codex_config_overrides":
            merged = dict(data.get(target_key, {}))
            if not isinstance(value, dict):
                raise ValueError(
                    f"codex_config_overrides must be an object in worker profile '{selected}'"
                )
            merged.update(value)
            data[target_key] = merged
        else:
            data[target_key] = value
    return AgentFarmConfig.from_dict(data), selected


def write_default_config(path: Path, *, force: bool = False) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"Config already exists: {path}")
    path.write_text(
        json.dumps(_config_document(default_config()), indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def write_local_config(repo_root: Path, data: dict[str, Any]) -> Path:
    """Atomically persist validated machine-local settings for future runs."""
    config = config_from_dict(data)
    path = (repo_root.resolve() / LOCAL_CONFIG_FILE).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(_config_document(config), stream, indent=2, ensure_ascii=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return path


def local_config_template() -> dict[str, Any]:
    return {
        CONFIG_SCHEMA_KEY: CONFIG_SCHEMA_VERSION,
        "agent_backend": "native",
        "supervisor_harness": "native",
        "worker_harness": "native",
        "supervisor_model": "your-high-capability-model",
        "supervisor_provider": "my-provider",
        "default_worker_profile": "cheap",
        "worker_profiles": {
            "cheap": {
                "display_name": "Economy Worker",
                "model": "your-cheap-worker-model",
                "provider": "my-provider",
                "timeout_seconds": 900,
            },
            "mid": {
                "display_name": "Balanced Worker",
                "model": "your-mid-worker-model",
                "provider": "my-provider",
                "timeout_seconds": 1800,
            },
        },
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
