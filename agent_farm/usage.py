from __future__ import annotations

import fnmatch
from copy import deepcopy
from typing import Any


PRICE_CATALOG_VERSION = "2026-08-02"

# USD per one million tokens. Entries are intentionally limited to prices that
# have an official public source. Unknown models stay unpriced until the user
# supplies an override; Agent Farm never guesses a price from a display name.
MODEL_PRICE_CATALOG: dict[str, dict[str, Any]] = {
    "openai/gpt-5.4": {
        "input": 2.50,
        "cached_input": 0.25,
        "output": 15.00,
        "source": "https://openai.com/index/introducing-gpt-5-4/",
    },
    "openai/gpt-5.4-pro": {
        "input": 30.00,
        "output": 180.00,
        "source": "https://openai.com/index/introducing-gpt-5-4/",
    },
    "openai/gpt-5.2": {
        "input": 1.75,
        "cached_input": 0.175,
        "output": 14.00,
        "source": "https://openai.com/index/introducing-gpt-5-4/",
    },
    "openai/gpt-5.2-pro": {
        "input": 21.00,
        "output": 168.00,
        "source": "https://openai.com/index/introducing-gpt-5-4/",
    },
    "anthropic/claude-opus-4-8*": {
        "input": 5.00,
        "cached_input": 0.50,
        "cache_write_input": 6.25,
        "output": 25.00,
        "source": "https://platform.claude.com/docs/en/about-claude/pricing",
    },
    "anthropic/claude-opus-4-[5-7]*": {
        "input": 5.00,
        "cached_input": 0.50,
        "cache_write_input": 6.25,
        "output": 25.00,
        "source": "https://platform.claude.com/docs/en/about-claude/pricing",
    },
    "anthropic/claude-sonnet-5*": {
        "input": 2.00,
        "cached_input": 0.20,
        "cache_write_input": 2.50,
        "output": 10.00,
        "effective_through": "2026-08-31",
        "source": "https://platform.claude.com/docs/en/about-claude/pricing",
    },
    "anthropic/claude-sonnet-4-[5-6]*": {
        "input": 3.00,
        "cached_input": 0.30,
        "cache_write_input": 3.75,
        "output": 15.00,
        "source": "https://platform.claude.com/docs/en/about-claude/pricing",
    },
    "anthropic/claude-haiku-4-5*": {
        "input": 1.00,
        "cached_input": 0.10,
        "cache_write_input": 1.25,
        "output": 5.00,
        "source": "https://platform.claude.com/docs/en/about-claude/pricing",
    },
    "gemini/gemini-3.6-flash*": {
        "input": 1.50,
        "cached_input": 0.15,
        "output": 7.50,
        "source": "https://ai.google.dev/gemini-api/docs/pricing",
    },
    "gemini/gemini-3.5-flash*": {
        "input": 1.50,
        "cached_input": 0.15,
        "output": 9.00,
        "source": "https://ai.google.dev/gemini-api/docs/pricing",
    },
    "deepseek/deepseek-v4-flash*": {
        "input": 0.14,
        "cached_input": 0.0028,
        "output": 0.28,
        "source": "https://api-docs.deepseek.com/quick_start/pricing/",
    },
    "deepseek/deepseek-v4-pro*": {
        "input": 0.435,
        "cached_input": 0.003625,
        "output": 0.87,
        "source": "https://api-docs.deepseek.com/quick_start/pricing/",
    },
    "ollama/*": {"input": 0.0, "cached_input": 0.0, "output": 0.0, "source": "local"},
    "lmstudio/*": {"input": 0.0, "cached_input": 0.0, "output": 0.0, "source": "local"},
}


def _integer(value: Any) -> int | None:
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, float) and value >= 0 and value.is_integer():
        return int(value)
    return None


def _first_integer(payload: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = _integer(payload.get(key))
        if value is not None:
            return value
    return 0


def normalize_usage(raw: dict[str, Any] | None) -> dict[str, int]:
    """Normalize common provider usage payloads to one stable token schema."""
    payload = raw if isinstance(raw, dict) else {}
    metadata = payload.get("usageMetadata")
    if isinstance(metadata, dict):
        payload = metadata

    input_tokens = _first_integer(
        payload,
        "input_tokens",
        "prompt_tokens",
        "promptTokenCount",
        "inputTokenCount",
    )
    output_tokens = _first_integer(
        payload,
        "output_tokens",
        "completion_tokens",
        "candidatesTokenCount",
        "outputTokenCount",
    )
    additive_cache_usage = any(
        key in payload for key in ("cache_read_input_tokens", "cache_creation_input_tokens")
    )
    cached_input_tokens = _first_integer(
        payload,
        "cache_read_input_tokens",
        "cached_input_tokens",
        "cachedContentTokenCount",
    )
    cache_write_input_tokens = _first_integer(payload, "cache_creation_input_tokens")

    for details_key in ("input_tokens_details", "prompt_tokens_details"):
        details = payload.get(details_key)
        if isinstance(details, dict):
            cached_input_tokens = max(
                cached_input_tokens,
                _first_integer(details, "cached_tokens", "cachedTokens"),
            )

    # Anthropic reports cache read/write tokens alongside base input_tokens;
    # include every billed input category in the normalized total.
    normalized_input = (
        input_tokens + cached_input_tokens + cache_write_input_tokens
        if additive_cache_usage
        else input_tokens
    )
    provider_total = _first_integer(payload, "total_tokens", "totalTokenCount")
    total_tokens = max(provider_total, normalized_input + output_tokens)
    return {
        "input_tokens": normalized_input,
        "cached_input_tokens": cached_input_tokens,
        "cache_write_input_tokens": cache_write_input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def _validated_price(raw: dict[str, Any]) -> dict[str, Any] | None:
    result = deepcopy(raw)
    for key in ("input", "cached_input", "cache_write_input", "output"):
        if key not in result:
            continue
        value = result[key]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            return None
        result[key] = float(value)
    if "input" not in result or "output" not in result:
        return None
    return result


def price_for_model(
    provider: str,
    model: str,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> tuple[str | None, dict[str, Any] | None]:
    route = f"{provider}/{model}".casefold()
    combined: dict[str, dict[str, Any]] = dict(MODEL_PRICE_CATALOG)
    for pattern, price in (overrides or {}).items():
        if isinstance(pattern, str) and isinstance(price, dict):
            combined[pattern.casefold()] = price

    if route in combined:
        return route, _validated_price(combined[route])
    for pattern, price in combined.items():
        if "*" in pattern or "?" in pattern or "[" in pattern:
            if fnmatch.fnmatchcase(route, pattern.casefold()):
                return pattern, _validated_price(price)
    return None, None


def estimate_cost_usd(
    provider: str,
    model: str,
    usage: dict[str, int],
    overrides: dict[str, dict[str, Any]] | None = None,
) -> tuple[float | None, str | None, dict[str, Any] | None]:
    pattern, price = price_for_model(provider, model, overrides)
    if price is None:
        return None, pattern, None
    cached = min(usage.get("cached_input_tokens", 0), usage.get("input_tokens", 0))
    cache_write = min(
        usage.get("cache_write_input_tokens", 0),
        max(0, usage.get("input_tokens", 0) - cached),
    )
    uncached = max(0, usage.get("input_tokens", 0) - cached - cache_write)
    input_cost = uncached * price["input"]
    cached_cost = cached * price.get("cached_input", price["input"])
    cache_write_cost = cache_write * price.get("cache_write_input", price["input"])
    output_cost = usage.get("output_tokens", 0) * price["output"]
    cost = (input_cost + cached_cost + cache_write_cost + output_cost) / 1_000_000
    return round(cost, 9), pattern, deepcopy(price)


def price_catalog(overrides: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    entries = deepcopy(MODEL_PRICE_CATALOG)
    entries.update(deepcopy(overrides or {}))
    return {"version": PRICE_CATALOG_VERSION, "currency": "USD", "entries": entries}
