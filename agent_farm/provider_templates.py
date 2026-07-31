from __future__ import annotations

from copy import deepcopy
from typing import Any


# Keep this catalog limited to providers that can use Agent Farm's implemented
# OpenAI Responses or Chat Completions wire formats. The entries are public
# connection metadata only; credentials are never stored here.
PROVIDER_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "id": "custom-openai-compatible",
        "name": "Custom OpenAI-compatible",
        "category": "Custom",
        "description": "Enter any OpenAI-compatible name, base URL, and API key.",
        "base_url": "https://api.example.com/v1",
        "env_key": "CUSTOM_OPENAI_API_KEY",
        "wire_api": "chat",
        "docs_url": "",
        "custom": True,
        "model_catalog": {"mode": "manual"},
        "reasoning": {"efforts": [], "thinking": []},
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "category": "Direct providers",
        "description": "Native Responses API for OpenAI models and tool calling.",
        "base_url": "https://api.openai.com/v1",
        "env_key": "OPENAI_API_KEY",
        "wire_api": "responses",
        "docs_url": "https://platform.openai.com/docs/api-reference/responses",
        "model_catalog": {"mode": "live", "parser": "openai"},
        "reasoning": {
            "efforts": ["none", "minimal", "low", "medium", "high", "xhigh"],
            "thinking": [],
        },
    },
    {
        "id": "anthropic",
        "name": "Anthropic Claude",
        "category": "Direct providers",
        "description": "Claude through Anthropic's OpenAI SDK compatibility layer.",
        "base_url": "https://api.anthropic.com/v1",
        "env_key": "ANTHROPIC_API_KEY",
        "wire_api": "chat",
        "docs_url": "https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk",
        "model_catalog": {"mode": "live", "parser": "anthropic"},
        # Anthropic's OpenAI compatibility layer ignores reasoning_effort. It
        # accepts the native thinking object through extra_body instead.
        "reasoning": {"efforts": [], "thinking": ["enabled", "disabled"]},
    },
    {
        "id": "gemini",
        "name": "Google Gemini",
        "category": "Direct providers",
        "description": "Gemini models through Google's OpenAI compatibility endpoint.",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "env_key": "GEMINI_API_KEY",
        "wire_api": "chat",
        "docs_url": "https://ai.google.dev/gemini-api/docs/openai",
        "model_catalog": {"mode": "live", "parser": "gemini"},
        "reasoning": {
            "efforts": ["none", "minimal", "low", "medium", "high"],
            "thinking": [],
        },
    },
    {
        "id": "xai",
        "name": "xAI",
        "category": "Direct providers",
        "description": "Grok models through xAI's OpenAI-compatible inference API.",
        "base_url": "https://api.x.ai/v1",
        "env_key": "XAI_API_KEY",
        "wire_api": "chat",
        "docs_url": "https://docs.x.ai/developers/rest-api-reference/inference",
        "model_catalog": {"mode": "live", "parser": "openai"},
        "reasoning": {"efforts": ["low", "medium", "high"], "thinking": []},
    },
    {
        "id": "mistral",
        "name": "Mistral AI",
        "category": "Direct providers",
        "description": "Mistral chat models using the OpenAI-compatible chat route.",
        "base_url": "https://api.mistral.ai/v1",
        "env_key": "MISTRAL_API_KEY",
        "wire_api": "chat",
        "docs_url": "https://docs.mistral.ai/api/",
        "model_catalog": {"mode": "live", "parser": "openai"},
        "reasoning": {"efforts": ["none", "high"], "thinking": []},
    },
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "category": "China providers",
        "description": "DeepSeek models through the official OpenAI-compatible API.",
        "base_url": "https://api.deepseek.com",
        "env_key": "DEEPSEEK_API_KEY",
        "wire_api": "chat",
        "default_model": "deepseek-v4-flash",
        "models": [
            {
                "id": "deepseek-v4-flash",
                "name": "DeepSeek V4 Flash",
                "description": "Economical default for Worker execution.",
            },
            {
                "id": "deepseek-v4-pro",
                "name": "DeepSeek V4 Pro",
                "description": "Higher-capability option for complex work.",
            },
        ],
        "docs_url": "https://api-docs.deepseek.com/",
        "model_catalog": {"mode": "live", "parser": "openai"},
        "reasoning": {
            "efforts": ["high", "max"],
            "thinking": ["enabled", "disabled"],
        },
    },
    {
        "id": "kimi",
        "name": "Kimi",
        "category": "China providers",
        "description": "Kimi models through Moonshot AI's OpenAI-compatible API.",
        "base_url": "https://api.moonshot.cn/v1",
        "env_key": "MOONSHOT_API_KEY",
        "wire_api": "chat",
        "docs_url": "https://platform.kimi.com/docs/overview",
        "model_catalog": {"mode": "live", "parser": "kimi"},
        "reasoning": {"efforts": [], "thinking": ["enabled", "disabled"]},
    },
    {
        "id": "qwen",
        "name": "Alibaba Cloud Qwen",
        "category": "China providers",
        "description": "Qwen and Model Studio models through DashScope compatibility mode.",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "env_key": "DASHSCOPE_API_KEY",
        "wire_api": "chat",
        "docs_url": "https://help.aliyun.com/zh/model-studio/base-url",
        "model_catalog": {"mode": "live", "parser": "openai"},
        "reasoning": {"efforts": [], "thinking": ["enabled", "disabled"]},
    },
    {
        "id": "zhipu",
        "name": "Zhipu GLM",
        "category": "China providers",
        "description": "GLM models through Zhipu's OpenAI-compatible API.",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "env_key": "ZAI_API_KEY",
        "wire_api": "chat",
        "docs_url": "https://docs.bigmodel.cn/cn/guide/develop/openai/introduction",
        "model_catalog": {"mode": "live", "parser": "openai"},
        "reasoning": {"efforts": [], "thinking": ["enabled", "disabled"]},
    },
    {
        "id": "doubao",
        "name": "Volcengine Ark / Doubao",
        "category": "China providers",
        "description": "Doubao and Ark models through the Beijing Responses endpoint.",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "env_key": "ARK_API_KEY",
        "wire_api": "responses",
        "docs_url": "https://www.volcengine.com/docs/82379/1795150",
        "model_catalog": {"mode": "live", "parser": "openai"},
        "reasoning": {"efforts": [], "thinking": []},
    },
    {
        "id": "siliconflow",
        "name": "SiliconFlow",
        "category": "Model gateways",
        "description": "Open models hosted by SiliconFlow through one compatible endpoint.",
        "base_url": "https://api.siliconflow.cn/v1",
        "env_key": "SILICONFLOW_API_KEY",
        "wire_api": "chat",
        "docs_url": "https://docs.siliconflow.cn/cn/userguide/quickstart",
        "model_catalog": {"mode": "live", "parser": "siliconflow"},
        "reasoning": {"efforts": [], "thinking": []},
    },
    {
        "id": "openrouter",
        "name": "OpenRouter",
        "category": "Model gateways",
        "description": "Route hundreds of models through one OpenAI-compatible API.",
        "base_url": "https://openrouter.ai/api/v1",
        "env_key": "OPENROUTER_API_KEY",
        "wire_api": "chat",
        "docs_url": "https://openrouter.ai/docs/quickstart",
        "model_catalog": {"mode": "live", "parser": "openrouter"},
        "reasoning": {"efforts": [], "thinking": []},
    },
    {
        "id": "groq",
        "name": "GroqCloud",
        "category": "Model gateways",
        "description": "Low-latency hosted models through Groq's compatible endpoint.",
        "base_url": "https://api.groq.com/openai/v1",
        "env_key": "GROQ_API_KEY",
        "wire_api": "chat",
        "docs_url": "https://console.groq.com/docs/openai",
        "model_catalog": {"mode": "live", "parser": "openai"},
        "reasoning": {"efforts": [], "thinking": []},
    },
    {
        "id": "together",
        "name": "Together AI",
        "category": "Model gateways",
        "description": "Open and proprietary models through Together's compatible API.",
        "base_url": "https://api.together.ai/v1",
        "env_key": "TOGETHER_API_KEY",
        "wire_api": "chat",
        "docs_url": "https://docs.together.ai/docs/inference/openai-compatibility",
        "model_catalog": {"mode": "live", "parser": "openai"},
        "reasoning": {"efforts": [], "thinking": []},
    },
    {
        "id": "fireworks",
        "name": "Fireworks AI",
        "category": "Model gateways",
        "description": "Hosted and deployed models through Fireworks OpenAI compatibility.",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "env_key": "FIREWORKS_API_KEY",
        "wire_api": "chat",
        "docs_url": "https://docs.fireworks.ai/tools-sdks/openai-compatibility",
        "model_catalog": {"mode": "live", "parser": "openai"},
        "reasoning": {"efforts": [], "thinking": []},
    },
    {
        "id": "ollama",
        "name": "Ollama",
        "category": "Local runtimes",
        "description": "Run local models through Ollama's OpenAI-compatible endpoint.",
        "base_url": "http://127.0.0.1:11434/v1",
        "env_key": "",
        "wire_api": "chat",
        "docs_url": "https://docs.ollama.com/api/openai-compatibility",
        "model_catalog": {"mode": "live", "parser": "openai"},
        "reasoning": {"efforts": [], "thinking": []},
    },
    {
        "id": "lmstudio",
        "name": "LM Studio",
        "category": "Local runtimes",
        "description": "Connect to the local LM Studio model server.",
        "base_url": "http://127.0.0.1:1234/v1",
        "env_key": "",
        "wire_api": "chat",
        "docs_url": "https://lmstudio.ai/docs/developer/openai-compat",
        "model_catalog": {"mode": "live", "parser": "lmstudio"},
        "reasoning": {"efforts": [], "thinking": []},
    },
)


def provider_templates() -> list[dict[str, Any]]:
    """Return a safe mutable copy for API serialization."""
    return deepcopy(list(PROVIDER_TEMPLATES))


def provider_config_from_template(template_id: str) -> dict[str, Any]:
    for template in PROVIDER_TEMPLATES:
        if template["id"] != template_id:
            continue
        return {
            "template_id": template["id"],
            "name": template["name"],
            "base_url": template["base_url"],
            "env_key": template["env_key"],
            "wire_api": template["wire_api"],
            "requires_openai_auth": bool(template["env_key"]),
        }
    raise KeyError(template_id)


def provider_template_for(
    provider_id: str,
    provider: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Match a configured route to an official template without guessing by name.

    Display names are user-editable, so treating a custom endpoint named "OpenAI"
    as the official OpenAI service would incorrectly remove its manual model field.
    """
    raw = provider if isinstance(provider, dict) else {}
    explicit = raw.get("template_id")
    normalized_base = str(raw.get("base_url") or "").rstrip("/").casefold()
    for template in PROVIDER_TEMPLATES:
        if template["id"] == explicit or template["id"] == provider_id:
            return deepcopy(template)
        template_base = str(template.get("base_url") or "").rstrip("/").casefold()
        if normalized_base and template_base == normalized_base:
            return deepcopy(template)
    return None
