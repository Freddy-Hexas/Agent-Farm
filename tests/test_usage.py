from __future__ import annotations

import unittest

from agent_farm.config import config_from_dict, default_config_json
from agent_farm.model_client import ModelRoute, ModelSession
from agent_farm.usage import (
    PRICE_CATALOG_VERSION,
    estimate_cost_usd,
    normalize_usage,
    price_for_model,
)


class UsageTests(unittest.TestCase):
    def test_normalizes_openai_chat_cached_token_usage(self):
        usage = normalize_usage(
            {
                "prompt_tokens": 120,
                "completion_tokens": 30,
                "total_tokens": 150,
                "prompt_tokens_details": {"cached_tokens": 20},
            }
        )
        self.assertEqual(
            usage,
            {
                "input_tokens": 120,
                "cached_input_tokens": 20,
                "cache_write_input_tokens": 0,
                "output_tokens": 30,
                "total_tokens": 150,
            },
        )

    def test_normalizes_anthropic_and_gemini_usage(self):
        anthropic = normalize_usage(
            {
                "input_tokens": 80,
                "cache_read_input_tokens": 15,
                "cache_creation_input_tokens": 5,
                "output_tokens": 10,
            }
        )
        gemini = normalize_usage(
            {
                "usageMetadata": {
                    "promptTokenCount": 50,
                    "candidatesTokenCount": 12,
                    "cachedContentTokenCount": 7,
                    "totalTokenCount": 62,
                }
            }
        )
        self.assertEqual(anthropic["input_tokens"], 100)
        self.assertEqual(anthropic["total_tokens"], 110)
        self.assertEqual(gemini["input_tokens"], 50)
        self.assertEqual(gemini["cached_input_tokens"], 7)
        self.assertEqual(gemini["total_tokens"], 62)

    def test_estimates_cost_with_catalog_and_custom_override(self):
        usage = normalize_usage(
            {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
        )
        cost, pattern, _ = estimate_cost_usd("deepseek", "deepseek-v4-flash", usage)
        self.assertEqual(cost, 0.42)
        self.assertEqual(pattern, "deepseek/deepseek-v4-flash*")

        override = {"custom/my-model": {"input": 1.0, "output": 2.0, "source": "contract"}}
        cost, pattern, price = estimate_cost_usd("custom", "my-model", usage, override)
        self.assertEqual(cost, 3.0)
        self.assertEqual(pattern, "custom/my-model")
        self.assertEqual(price["source"], "contract")

    def test_unknown_models_are_not_assigned_guessed_prices(self):
        pattern, price = price_for_model("custom", "mystery-model")
        self.assertIsNone(pattern)
        self.assertIsNone(price)

    def test_config_validates_price_overrides(self):
        raw = default_config_json()
        raw["model_price_overrides"] = {
            "gateway/economy-*": {"input": 0.1, "output": 0.4, "source": "invoice"}
        }
        config = config_from_dict(raw)
        self.assertEqual(config.model_price_overrides["gateway/economy-*"]["output"], 0.4)

        raw["model_price_overrides"]["gateway/bad"] = {"input": -1, "output": 1}
        with self.assertRaisesRegex(ValueError, "must be non-negative"):
            config_from_dict(raw)

    def test_model_request_event_records_normalized_usage_latency_retry_and_cost(self):
        events = []

        def transport(url, headers, payload, timeout):
            return {
                "id": "chat-usage",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 1_000, "completion_tokens": 200},
            }

        route = ModelRoute(
            provider_id="deepseek",
            template_id="deepseek",
            model="deepseek-v4-flash",
            base_url="https://example.com",
            wire_api="chat",
            headers={},
            extra_query={},
            request_max_retries=0,
        )
        reply = ModelSession(
            route=route,
            system_prompt="system",
            timeout_seconds=None,
            transport=transport,
            event_callback=events.append,
            usage_context={"agent_kind": "worker", "farm_id": "farm-1"},
        ).send(prompt="hello")

        completed = next(event for event in events if event["type"] == "model.request.completed")
        self.assertTrue(completed["request_id"].startswith("model-"))
        self.assertGreaterEqual(completed["latency_ms"], 0)
        self.assertEqual(completed["retry_count"], 0)
        self.assertEqual(completed["agent_kind"], "worker")
        self.assertEqual(reply.usage["input_tokens"], 1_000)
        self.assertEqual(reply.usage["output_tokens"], 200)
        self.assertEqual(reply.usage["price_catalog_version"], PRICE_CATALOG_VERSION)
        self.assertIsNotNone(reply.usage["estimated_cost_usd"])


if __name__ == "__main__":
    unittest.main()
