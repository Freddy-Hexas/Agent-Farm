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

    def test_custom_endpoint_named_openai_remains_manual(self) -> None:
        config = AgentFarmConfig(
            model_providers={
                "Krill": {
                    "name": "OpenAI",
                    "base_url": "https://proxy.example.com/v1",
                    "env_key": "PROXY_KEY",
                    "wire_api": "responses",
                }
            }
        )
        with self.assertRaisesRegex(ProviderCatalogError, "Enter its Model ID manually"):
            discover_provider_models(
                config=config,
                repo_root=Path.cwd(),
                provider_id="Krill",
                transport=lambda *_: {},
            )


if __name__ == "__main__":
    unittest.main()
