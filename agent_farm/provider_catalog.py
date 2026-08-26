from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .model_client import ModelClientError, ModelRoute, resolve_model_route
from .models import AgentFarmConfig
from .provider_templates import provider_template_for


class ProviderCatalogError(ValueError):
    pass


CatalogTransport = Callable[[str, dict[str, str], int], dict[str, Any]]


def _catalog_transport(url: str, headers: dict[str, str], timeout_seconds: int) -> dict[str, Any]:
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2_000]
        if exc.code in {401, 403}:
            detail = "Check the saved API key and its model-list permission."
        raise ProviderCatalogError(
            f"The provider rejected the model catalog request (HTTP {exc.code}). {detail}"
        ) from exc
    except URLError as exc:
        raise ProviderCatalogError(f"Could not reach the provider model catalog: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ProviderCatalogError("The provider model catalog timed out.") from exc
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProviderCatalogError("The provider model catalog returned invalid JSON.") from exc
    if not isinstance(decoded, dict):
        raise ProviderCatalogError("The provider model catalog returned a non-object response.")
    return decoded


def _with_query(url: str, values: dict[str, Any]) -> str:
    parsed = urlsplit(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update({str(key): str(value) for key, value in values.items() if value is not None})
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def _models_endpoint(route: ModelRoute, template_id: str) -> tuple[str, dict[str, str]]:
    headers = dict(route.headers)
    base = route.base_url.rstrip("/")
    if base.endswith("/responses"):
        base = base.removesuffix("/responses")
    if base.endswith("/chat/completions"):
        base = base.removesuffix("/chat/completions")

    if template_id == "gemini":
        authorization = headers.pop("Authorization", "")
        api_key = authorization.removeprefix("Bearer ").strip()
        if not api_key:
            raise ProviderCatalogError("Google Gemini needs an API key before models can be loaded.")
        return _with_query(
            "https://generativelanguage.googleapis.com/v1beta/models",
            {"pageSize": 1000, "key": api_key},
        ), headers
    if template_id == "lmstudio":
        parsed = urlsplit(base)
        return urlunsplit((parsed.scheme, parsed.netloc, "/api/v1/models", "", "")), headers
    if template_id == "anthropic":
        authorization = headers.pop("Authorization", "")
        if authorization:
            headers["x-api-key"] = authorization.removeprefix("Bearer ").strip()
        headers["anthropic-version"] = "2023-06-01"
    endpoint = base if base.endswith("/models") else base + "/models"
    if template_id == "anthropic":
        endpoint = _with_query(endpoint, {"limit": 1000})
    if template_id == "siliconflow":
        endpoint = _with_query(endpoint, {"type": "text", "sub_type": "chat"})
    return endpoint, headers


def _response_items(payload: dict[str, Any], parser: str) -> list[dict[str, Any]]:
    if parser == "gemini":
        raw = payload.get("models") or []
    elif parser == "lmstudio":
        raw = payload.get("models") or []
    else:
        raw = payload.get("data")
        if raw is None:
            raw = payload.get("models") or []
        if isinstance(raw, dict):
            raw = raw.get("data") or raw.get("models") or []
    if not isinstance(raw, list):
        raise ProviderCatalogError("The provider model catalog did not contain a model list.")
    return [item for item in raw if isinstance(item, dict)]


def _is_compatible_model(item: dict[str, Any], parser: str) -> bool:
    if item.get("archived") is True:
        return False
    if parser == "gemini":
        actions = item.get("supportedGenerationMethods") or item.get("supportedActions") or []
        return not actions or "generateContent" in actions
    if parser == "lmstudio":
        return str(item.get("type") or "").casefold() == "llm"

    capabilities = item.get("capabilities")
    if isinstance(capabilities, dict) and capabilities.get("completion_chat") is False:
        return False
    raw_type = str(item.get("type") or item.get("model_type") or "").casefold()
    if raw_type in {"embedding", "embeddings", "rerank", "reranker", "image", "audio", "moderation"}:
        return False
    architecture = item.get("architecture")
    if isinstance(architecture, dict):
        output_modalities = architecture.get("output_modalities") or []
        if output_modalities and "text" not in output_modalities:
            return False
    model_id = str(item.get("id") or item.get("name") or item.get("key") or "").casefold()
    return bool(model_id) and not any(
        marker in model_id
        for marker in ("embedding", "embed-", "rerank", "moderation", "tts-", "whisper")
    )


def _model_id(item: dict[str, Any], parser: str) -> str:
    raw = item.get("key") if parser == "lmstudio" else item.get("id")
    if not raw:
        raw = item.get("name")
    model_id = str(raw or "").strip()
    if parser == "gemini" and model_id.startswith("models/"):
        model_id = model_id.removeprefix("models/")
    return model_id


def _empty_reasoning() -> dict[str, Any]:
    return {"efforts": [], "thinking": [], "mandatory": False}


def _template_reasoning(template: dict[str, Any]) -> dict[str, Any]:
    raw = template.get("reasoning") or {}
    return {
        "efforts": list(raw.get("efforts") or []),
        "thinking": list(raw.get("thinking") or []),
        "mandatory": bool(raw.get("mandatory", False)),
    }


def _family_reasoning(model_id: str) -> dict[str, Any]:
    """Infer controls for gateway/local models from their upstream model family."""
    lowered = model_id.casefold()
    if "deepseek-v4" in lowered:
        return {
            "efforts": ["high", "max"],
            "thinking": ["enabled", "disabled"],
            "mandatory": False,
        }
    if "deepseek-r1" in lowered or "qwq" in lowered:
        return {"efforts": [], "thinking": ["enabled"], "mandatory": True}
    if "kimi-k2-thinking" in lowered:
        return {"efforts": [], "thinking": ["enabled"], "mandatory": True}
    if any(token in lowered for token in ("kimi-k2.5", "kimi-k2-5", "glm-5", "glm-4.7", "glm-4.6", "glm-4.5", "deepseek-v3.1", "deepseek-v3.2", "hunyuan-a13")):
        return {"efforts": [], "thinking": ["enabled", "disabled"], "mandatory": False}
    if "gpt-oss" in lowered:
        return {"efforts": ["low", "medium", "high"], "thinking": [], "mandatory": False}
    if "qwen3" in lowered:
        return {"efforts": [], "thinking": ["enabled", "disabled"], "mandatory": False}
    return _empty_reasoning()


def _reasoning_for_model(
    template: dict[str, Any],
    model_id: str,
    item: dict[str, Any],
) -> dict[str, Any]:
    template_id = str(template.get("id") or "")
    lowered = model_id.casefold()

    if template_id == "lmstudio":
        capabilities = item.get("capabilities") or {}
        raw = capabilities.get("reasoning") if isinstance(capabilities, dict) else None
        if not isinstance(raw, dict):
            return _empty_reasoning()
        allowed = [str(value) for value in raw.get("allowed_options") or []]
        efforts = [value for value in allowed if value in {"low", "medium", "high"}]
        thinking: list[str] = []
        if "on" in allowed:
            thinking.append("enabled")
        if "off" in allowed:
            thinking.append("disabled")
        return {
            "efforts": efforts,
            "thinking": thinking,
            "mandatory": allowed == ["on"],
            "default": raw.get("default"),
        }

    if template_id == "openrouter":
        supported = item.get("supported_parameters") or []
        raw = item.get("reasoning")
        if "reasoning" not in supported and not isinstance(raw, dict):
            return _empty_reasoning()
        raw = raw if isinstance(raw, dict) else {}
        efforts = [
            str(value)
            for value in raw.get("supported_efforts") or []
            if str(value) in {"none", "default", "minimal", "low", "medium", "high", "xhigh", "max"}
        ]
        if not efforts:
            efforts = _family_reasoning(model_id)["efforts"]
        return {
            "efforts": efforts,
            "thinking": ["enabled", "disabled"],
            "mandatory": bool(raw.get("mandatory", False)),
            "default": raw.get("default_effort"),
        }

    if template_id == "kimi":
        supports = item.get("supports_reasoning")
        if supports is False:
            return _empty_reasoning()
        result = _template_reasoning(template)
        if "thinking" in lowered and not any(token in lowered for token in ("k2.5", "k2-5", "k2.6", "k2-6")):
            result["thinking"] = ["enabled"]
            result["mandatory"] = True
        return result

    if template_id == "openai":
        if not any(token in lowered for token in ("gpt-5", "codex", "o1", "o3", "o4")):
            return _empty_reasoning()
        if "pro" in lowered and "gpt-5" in lowered:
            return {"efforts": ["high"], "thinking": [], "mandatory": True}
        efforts = ["low", "medium", "high"]
        if "gpt-5" in lowered:
            efforts.insert(0, "none")
        if any(token in lowered for token in ("codex-max", "gpt-5.4", "gpt-5.5", "gpt-5.6")):
            efforts.append("xhigh")
        return {"efforts": efforts, "thinking": [], "mandatory": False}

    if template_id == "gemini":
        if "2.0" in lowered or "1.5" in lowered:
            return _empty_reasoning()
        if "2.5-pro" in lowered or ("3" in lowered and "pro" in lowered):
            return {"efforts": ["low", "high"], "thinking": [], "mandatory": False}
        return _template_reasoning(template)

    if template_id == "xai":
        return _template_reasoning(template) if any(token in lowered for token in ("grok-4", "reason")) else _empty_reasoning()

    if template_id == "mistral":
        if any(token in lowered for token in ("mistral-small-latest", "mistral-medium-3-5")):
            return _template_reasoning(template)
        if "magistral" in lowered:
            return {"efforts": [], "thinking": ["enabled"], "mandatory": True}
        return _empty_reasoning()

    if template_id == "anthropic":
        return _template_reasoning(template) if any(token in lowered for token in ("claude-4", "claude-5", "opus-4", "sonnet-4")) else _empty_reasoning()

    if template_id == "qwen":
        return _template_reasoning(template) if any(token in lowered for token in ("qwen3", "qwq")) else _empty_reasoning()

    if template_id == "groq":
        if "qwen3" in lowered:
            return {"efforts": ["none", "default"], "thinking": [], "mandatory": False}
        return _family_reasoning(model_id)

    if template_id == "together":
        if "deepseek-v4" in lowered:
            return {"efforts": ["high", "max"], "thinking": [], "mandatory": False}
        return _family_reasoning(model_id)

    if template_id in {"siliconflow", "fireworks", "ollama"}:
        return _family_reasoning(model_id)

    if template_id == "custom-openai-compatible":
        raw_reasoning = item.get("reasoning")
        if isinstance(raw_reasoning, dict):
            allowed_efforts = {"none", "default", "minimal", "low", "medium", "high", "xhigh", "max"}
            raw_efforts = raw_reasoning.get("supported_efforts") or raw_reasoning.get("efforts") or []
            efforts = [str(value) for value in raw_efforts if str(value) in allowed_efforts]
            raw_thinking = raw_reasoning.get("thinking") or raw_reasoning.get("modes") or []
            thinking = [str(value) for value in raw_thinking if str(value) in {"enabled", "disabled"}]
            if efforts or thinking:
                return {
                    "efforts": efforts,
                    "thinking": thinking,
                    "mandatory": bool(raw_reasoning.get("mandatory", False)),
                    "default": raw_reasoning.get("default") or raw_reasoning.get("default_effort"),
                }
        # A gateway usually exposes only an ID and a display name. Use the
        # family in that ID to avoid offering controls that the upstream model
        # cannot understand, while retaining the complete neutral set for
        # aliases that do not identify a family.
        if "qwen" in lowered or "claude" in lowered:
            return {"efforts": [], "thinking": ["enabled", "disabled"], "mandatory": False}
        if "deepseek" in lowered:
            family = _family_reasoning(model_id)
            return family if family["efforts"] or family["thinking"] else {
                "efforts": ["high", "max"],
                "thinking": ["enabled", "disabled"],
                "mandatory": False,
            }
        if any(token in lowered for token in ("kimi", "glm", "minimax", "magistral")):
            return {"efforts": [], "thinking": ["enabled", "disabled"], "mandatory": False}
        if any(token in lowered for token in ("gpt", "codex", "o1", "o3", "o4")):
            return {
                "efforts": ["none", "default", "minimal", "low", "medium", "high", "xhigh", "max"],
                "thinking": [],
                "mandatory": False,
            }

    return _template_reasoning(template)


def _normalized_model(item: dict[str, Any], parser: str, template: dict[str, Any]) -> dict[str, Any]:
    model_id = _model_id(item, parser)
    display_name = str(
        item.get("display_name")
        or item.get("displayName")
        or item.get("name")
        or model_id
    ).removeprefix("models/")
    description = item.get("description")
    normalized: dict[str, Any] = {
        "id": model_id,
        "name": display_name,
        "reasoning": _reasoning_for_model(template, model_id, item),
    }
    if isinstance(description, str) and description.strip():
        normalized["description"] = description.strip()
    return normalized


def _fallback_catalog(provider_id: str, template: dict[str, Any], warning: str) -> dict[str, Any] | None:
    raw_models = template.get("models") or []
    if not raw_models:
        return None
    models = []
    for raw in raw_models:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        item = deepcopy(raw)
        item["reasoning"] = _reasoning_for_model(template, str(raw["id"]), raw)
        models.append(item)
    return {
        "provider_id": provider_id,
        "template_id": template["id"],
        "source": "fallback",
        "warning": warning,
        "models": models,
        "model_count": len(models),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def _manual_catalog(provider_id: str, template: dict[str, Any], warning: str) -> dict[str, Any]:
    return {
        "provider_id": provider_id,
        "template_id": template["id"],
        "source": "manual",
        "warning": warning,
        "models": [],
        "model_count": 0,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def discover_provider_models(
    *,
    config: AgentFarmConfig,
    repo_root: Path,
    provider_id: str,
    timeout_seconds: int = 15,
    transport: CatalogTransport | None = None,
) -> dict[str, Any]:
    provider: dict[str, Any] = {}
    configured = config.model_providers.get(provider_id)
    if isinstance(configured, dict):
        provider.update(configured)
    template = provider_template_for(provider_id, provider)
    if template is None:
        raise ProviderCatalogError(
            "This provider has no model catalog template. Enter its Model ID manually."
        )

    try:
        route = resolve_model_route(
            config=config,
            repo_root=repo_root,
            provider_id=provider_id,
            model="__catalog__",
        )
        endpoint, headers = _models_endpoint(route, str(template["id"]))
        parser = str((template.get("model_catalog") or {}).get("parser") or "openai")
        payload = (transport or _catalog_transport)(endpoint, headers, timeout_seconds)
        raw_items = _response_items(payload, parser)
        models = [
            _normalized_model(item, parser, template)
            for item in raw_items
            if _is_compatible_model(item, parser) and _model_id(item, parser)
        ]
    except (ModelClientError, ProviderCatalogError) as exc:
        fallback = _fallback_catalog(provider_id, template, str(exc))
        if fallback is not None:
            return fallback
        if template.get("custom"):
            # Gateways are allowed to omit /models or protect it with a
            # separate permission. The route remains usable with an exact
            # manually entered model ID in that case.
            return _manual_catalog(provider_id, template, str(exc))
        raise ProviderCatalogError(str(exc)) from exc

    unique = {model["id"]: model for model in models}
    ordered = sorted(unique.values(), key=lambda model: (str(model["name"]).casefold(), model["id"]))
    if not ordered:
        if template.get("custom"):
            return _manual_catalog(
                provider_id,
                template,
                "The provider returned no compatible text-generation models.",
            )
        raise ProviderCatalogError("The provider returned no compatible text-generation models.")
    return {
        "provider_id": provider_id,
        "template_id": template["id"],
        "source": "live",
        "models": ordered,
        "model_count": len(ordered),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
