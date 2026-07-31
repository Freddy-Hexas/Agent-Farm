from __future__ import annotations

import unittest

from agent_farm.provider_templates import (
    PROVIDER_TEMPLATES,
    provider_config_from_template,
    provider_templates,
)


class ProviderTemplateTests(unittest.TestCase):
    def test_catalog_contains_supported_unique_templates_and_custom_option(self) -> None:
        templates = provider_templates()
        ids = [template["id"] for template in templates]

        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(templates), 15)
        self.assertIn("custom-openai-compatible", ids)
        self.assertTrue(
            {
                "openai",
                "anthropic",
                "gemini",
                "deepseek",
                "kimi",
                "qwen",
                "zhipu",
                "doubao",
                "openrouter",
                "ollama",
                "lmstudio",
            }.issubset(ids)
        )
        for template in templates:
            self.assertIn(template["wire_api"], {"responses", "chat"})
            self.assertTrue(template["base_url"].startswith(("http://", "https://")))
            self.assertNotIn("api_key", template)

    def test_catalog_returns_a_copy_and_projects_only_provider_config_fields(self) -> None:
        templates = provider_templates()
        templates[0]["name"] = "mutated"
        self.assertNotEqual(PROVIDER_TEMPLATES[0]["name"], "mutated")

        config = provider_config_from_template("openai")
        self.assertEqual(config["base_url"], "https://api.openai.com/v1")
        self.assertEqual(config["wire_api"], "responses")
        self.assertTrue(config["requires_openai_auth"])

    def test_deepseek_template_exposes_current_worker_model_defaults(self) -> None:
        deepseek = next(item for item in provider_templates() if item["id"] == "deepseek")
        self.assertEqual(deepseek["model_catalog"]["mode"], "live")
        self.assertEqual(deepseek["reasoning"]["efforts"], ["high", "max"])
        self.assertNotIn("xhigh", deepseek["reasoning"]["efforts"])
        self.assertEqual(deepseek["default_model"], "deepseek-v4-flash")
        self.assertEqual(
            [model["id"] for model in deepseek["models"]],
            ["deepseek-v4-flash", "deepseek-v4-pro"],
        )

    def test_provider_reasoning_defaults_follow_official_wire_controls(self) -> None:
        templates = {item["id"]: item for item in provider_templates()}
        self.assertEqual(templates["mistral"]["reasoning"]["efforts"], ["none", "high"])
        self.assertEqual(templates["anthropic"]["reasoning"]["thinking"], ["enabled", "disabled"])
        self.assertEqual(templates["doubao"]["reasoning"], {"efforts": [], "thinking": []})


if __name__ == "__main__":
    unittest.main()
