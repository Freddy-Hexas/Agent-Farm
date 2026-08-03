import unittest

from agent_farm.model_client import ModelRoute, ModelSession
from agent_farm.provider_templates import PROVIDER_TEMPLATES


class ProviderContractTests(unittest.TestCase):
    def test_every_bundled_provider_satisfies_the_mocked_streaming_contract(self) -> None:
        for template in PROVIDER_TEMPLATES:
            with self.subTest(provider=template["id"]):
                events: list[dict] = []
                requests: list[dict] = []
                wire_api = template["wire_api"]
                route = ModelRoute(
                    provider_id=template["id"],
                    template_id=template["id"],
                    model=template.get("default_model", "contract-test-model"),
                    base_url=template["base_url"],
                    wire_api=wire_api,
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    extra_query={},
                    request_max_retries=0,
                )

                def streaming_transport(
                    url, headers, payload, timeout, selected_wire_api, callback
                ):
                    requests.append(
                        {
                            "url": url,
                            "headers": headers,
                            "payload": payload,
                            "timeout": timeout,
                            "wire_api": selected_wire_api,
                        }
                    )
                    callback({"type": "model.output.delta", "delta": "Hel"})
                    callback({"type": "model.output.delta", "delta": "lo"})
                    if selected_wire_api == "responses":
                        return {
                            "id": "response-contract",
                            "output": [
                                {
                                    "type": "message",
                                    "role": "assistant",
                                    "content": [{"type": "output_text", "text": "Hello"}],
                                }
                            ],
                            "usage": {"input_tokens": 2, "output_tokens": 1},
                        }
                    return {
                        "id": "chat-contract",
                        "choices": [
                            {"message": {"role": "assistant", "content": "Hello"}}
                        ],
                        "usage": {"prompt_tokens": 2, "completion_tokens": 1},
                    }

                reply = ModelSession(
                    route=route,
                    system_prompt="Contract test",
                    timeout_seconds=None,
                    event_callback=events.append,
                    streaming_transport=streaming_transport,
                ).send(prompt="Return a greeting")

                self.assertEqual(reply.text, "Hello")
                self.assertEqual(len(requests), 1)
                request = requests[0]
                self.assertIsNone(request["timeout"])
                self.assertEqual(request["wire_api"], wire_api)
                self.assertEqual(request["payload"]["model"], route.model)
                self.assertTrue(request["url"].endswith(
                    "/responses" if wire_api == "responses" else "/chat/completions"
                ))
                self.assertIn("X-Client-Request-Id", request["headers"])
                self.assertEqual(
                    [event.get("delta") for event in events if event["type"] == "model.output.delta"],
                    ["Hel", "lo"],
                )
                self.assertEqual(events[0]["type"], "model.request.started")
                self.assertEqual(events[-1]["type"], "model.request.completed")
                self.assertTrue(
                    all(
                        event.get("provider", template["id"]) == template["id"]
                        for event in (events[0], events[-1])
                    )
                )

    def test_provider_templates_expose_only_supported_wire_contracts(self) -> None:
        provider_ids = [template["id"] for template in PROVIDER_TEMPLATES]
        self.assertEqual(len(provider_ids), len(set(provider_ids)))
        for template in PROVIDER_TEMPLATES:
            with self.subTest(provider=template["id"]):
                self.assertIn(template["wire_api"], {"chat", "responses"})
                self.assertTrue(template["base_url"].startswith(("http://", "https://")))
                self.assertIn(template["model_catalog"]["mode"], {"live", "manual"})
                self.assertIsInstance(template["reasoning"]["efforts"], list)
                self.assertIsInstance(template["reasoning"]["thinking"], list)


if __name__ == "__main__":
    unittest.main()
