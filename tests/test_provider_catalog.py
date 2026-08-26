from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from agent_farm.models import AgentFarmConfig
from agent_farm.provider_catalog import ProviderCatalogError, discover_provider_models


class ProviderCatalogTests(unittest.TestCase):
    def test_deepseek_lists_every_returned_model_with_only_official_efforts(self) -> None:
        config = AgentFarmConfig(
            model_providers={
                "deepseek": {
                    "template_id": "deepseek",
                    "base_url": "https://api.deepseek.com",
                    "env_key": "DEEPSEEK_API_KEY",
                    "wire_api": "chat",
                    "requires_openai_auth": True,
                }
            }
        )

        def transport(url, headers, timeout):
            self.assertEqual(url, "https://api.deepseek.com/models")
            self.assertEqual(headers["Authorization"], "Bearer test-key")
            return {
                "data": [
                    {"id": "deepseek-v4-flash"},
                    {"id": "deepseek-v4-pro"},
                ]
            }

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ", {"DEEPSEEK_API_KEY": "test-key"}
        ):
            result = discover_provider_models(
                config=config,
                repo_root=Path(tmp),
                provider_id="deepseek",
                transport=transport,
            )

        self.assertEqual(result["source"], "live")
        self.assertEqual(
            {model["id"] for model in result["models"]},
            {"deepseek-v4-flash", "deepseek-v4-pro"},
        )
        for model in result["models"]:
            self.assertEqual(model["reasoning"]["efforts"], ["high", "max"])
            self.assertNotIn("xhigh", model["reasoning"]["efforts"])

    def test_gemini_uses_native_catalog_and_filters_non_generation_models(self) -> None:
        config = AgentFarmConfig(
            model_providers={
                "gemini": {
                    "template_id": "gemini",
                    "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                    "env_key": "GEMINI_API_KEY",
                    "wire_api": "chat",
                    "requires_openai_auth": True,
                }
            }
        )

        def transport(url, headers, timeout):
            self.assertIn("/v1beta/models?", url)
            self.assertIn("key=test-key", url)
            self.assertNotIn("Authorization", headers)
            return {
                "models": [
                    {
                        "name": "models/gemini-3.6-flash",
                        "displayName": "Gemini 3.6 Flash",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                    {
                        "name": "models/text-embedding-005",
                        "supportedGenerationMethods": ["embedContent"],
                    },
                ]
            }

        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}):
            result = discover_provider_models(
                config=config,
                repo_root=Path.cwd(),
                provider_id="gemini",
                transport=transport,
            )

        self.assertEqual([model["id"] for model in result["models"]], ["gemini-3.6-flash"])

    def test_openrouter_preserves_model_specific_reasoning_metadata(self) -> None:
        config = AgentFarmConfig(
            model_providers={
                "openrouter": {
                    "template_id": "openrouter",
                    "base_url": "https://openrouter.ai/api/v1",
                    "env_key": "OPENROUTER_API_KEY",
                    "wire_api": "chat",
                    "requires_openai_auth": True,
                }
            }
        )

        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}):
            result = discover_provider_models(
                config=config,
                repo_root=Path.cwd(),
                provider_id="openrouter",
                transport=lambda *_: {
                    "data": [
                        {
                            "id": "vendor/reasoning-model",
                            "name": "Reasoning model",
                            "supported_parameters": ["reasoning"],
                            "reasoning": {
                                "supported_efforts": ["low", "high"],
                                "default_effort": "high",
                                "mandatory": True,
                            },
                        }
                    ]
                },
            )

        reasoning = result["models"][0]["reasoning"]
        self.assertEqual(reasoning["efforts"], ["low", "high"])
        self.assertEqual(reasoning["default"], "high")
        self.assertTrue(reasoning["mandatory"])

    def test_lmstudio_native_catalog_filters_embeddings_and_exposes_allowed_reasoning(self) -> None:
        config = AgentFarmConfig()

        def transport(url, headers, timeout):
            self.assertEqual(url, "http://127.0.0.1:1234/api/v1/models")
            return {
                "models": [
                    {
                        "type": "llm",
                        "key": "openai/gpt-oss-20b",
                        "display_name": "GPT OSS 20B",
                        "capabilities": {
                            "reasoning": {
                                "allowed_options": ["low", "medium", "high"],
                                "default": "medium",
                            }
                        },
                    },
                    {"type": "embedding", "key": "embedding-model"},
                ]
            }

        result = discover_provider_models(
            config=config,
            repo_root=Path.cwd(),
            provider_id="lmstudio",
            transport=transport,
        )
        self.assertEqual([model["id"] for model in result["models"]], ["openai/gpt-oss-20b"])
        self.assertEqual(result["models"][0]["reasoning"]["efforts"], ["low", "medium", "high"])

    def test_custom_openai_compatible_gateway_loads_models_and_keeps_manual_fallback(self) -> None:
        config = AgentFarmConfig(
            model_providers={
                "Krill": {
                    "template_id": "custom-openai-compatible",
                    "name": "OpenAI",
                    "base_url": "https://proxy.example.com/v1",
                    "env_key": "PROXY_KEY",
                    "wire_api": "responses",
                }
            }
        )
        with patch.dict("os.environ", {"PROXY_KEY": "test-key"}):
            result = discover_provider_models(
                config=config,
                repo_root=Path.cwd(),
                provider_id="Krill",
                transport=lambda *_: {"data": [{"id": "anthropic/claude-sonnet-4.5"}]},
            )

        self.assertEqual(result["source"], "live")
        self.assertEqual(result["models"][0]["id"], "anthropic/claude-sonnet-4.5")
        self.assertEqual(
            result["models"][0]["reasoning"],
            {
                "efforts": [],
                "thinking": ["enabled", "disabled"],
                "mandatory": False,
            },
        )

        with patch.dict("os.environ", {"PROXY_KEY": "test-key"}):
            manual = discover_provider_models(
                config=config,
                repo_root=Path.cwd(),
                provider_id="Krill",
                transport=lambda *_: (_ for _ in ()).throw(ProviderCatalogError("catalog disabled")),
            )
        self.assertEqual(manual["source"], "manual")
        self.assertEqual(manual["models"], [])

    def test_unidentified_gateway_is_discovered_without_an_official_template(self) -> None:
        config = AgentFarmConfig(
            model_providers={
                "relay": {
                    "name": "Team gateway",
                    "base_url": "https://relay.example.com/v1",
                    "env_key": "RELAY_KEY",
                    "wire_api": "chat",
                }
            }
        )
        with patch.dict("os.environ", {"RELAY_KEY": "test-key"}):
            result = discover_provider_models(
                config=config,
                repo_root=Path.cwd(),
                provider_id="relay",
                transport=lambda *_: {
                    "data": [
                        {"id": "qwen/qwen3-235b-a22b"},
                        {"id": "anthropic/claude-sonnet-4.5"},
                        {"id": "openai/gpt-5.5"},
                    ]
                },
            )

        self.assertEqual(result["template_id"], "custom-openai-compatible")
        self.assertEqual(
            {model["id"] for model in result["models"]},
            {"qwen/qwen3-235b-a22b", "anthropic/claude-sonnet-4.5", "openai/gpt-5.5"},
        )

        with patch.dict("os.environ", {"RELAY_KEY": "test-key"}):
            manual = discover_provider_models(
                config=config,
                repo_root=Path.cwd(),
                provider_id="relay",
                transport=lambda *_: {"data": []},
            )
        self.assertEqual(manual["source"], "manual")
        self.assertEqual(manual["model_count"], 0)

    def test_gateway_reasoning_metadata_overrides_family_fallback(self) -> None:
        config = AgentFarmConfig(
            model_providers={
                "relay": {
                    "template_id": "custom-openai-compatible",
                    "base_url": "https://relay.example.com/v1",
                    "requires_openai_auth": False,
                }
            }
        )
        result = discover_provider_models(
            config=config,
            repo_root=Path.cwd(),
            provider_id="relay",
            transport=lambda *_: {
                "data": [
                    {
                        "id": "vendor/unknown-reasoning-model",
                        "reasoning": {
                            "supported_efforts": ["low", "high"],
                            "thinking": ["enabled"],
                            "mandatory": True,
                            "default_effort": "high",
                        },
                    }
                ]
            },
        )
        self.assertEqual(
            result["models"][0]["reasoning"],
            {
                "efforts": ["low", "high"],
                "thinking": ["enabled"],
                "mandatory": True,
                "default": "high",
            },
        )


if __name__ == "__main__":
    unittest.main()
