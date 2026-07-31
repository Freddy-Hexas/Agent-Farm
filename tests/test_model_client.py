import tempfile
import unittest
from pathlib import Path

from agent_farm.model_client import ModelRoute, ModelSession, resolve_model_route
from agent_farm.models import AgentFarmConfig


class ModelClientTests(unittest.TestCase):
    @staticmethod
    def _chat_route(template_id: str) -> ModelRoute:
        return ModelRoute(
            provider_id=template_id,
            template_id=template_id,
            model="test-model",
            base_url="https://example.com/v1",
            wire_api="chat",
            headers={},
            extra_query={},
            request_max_retries=0,
        )

    @staticmethod
    def _capture_chat_payload(
        route: ModelRoute,
        *,
        reasoning_mode: str | None = None,
        reasoning_effort: str | None = None,
    ) -> dict:
        requests = []

        def transport(url, headers, payload, timeout):
            requests.append(payload)
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

        ModelSession(
            route=route,
            system_prompt="System",
            timeout_seconds=5,
            reasoning_mode=reasoning_mode,
            reasoning_effort=reasoning_effort,
            transport=transport,
        ).send(prompt="Hello")
        return requests[0]

    def test_responses_session_carries_tool_result_into_next_turn(self):
        requests = []

        def transport(url, headers, payload, timeout):
            requests.append(payload)
            if len(requests) == 1:
                return {
                    "id": "response-1",
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call-1",
                            "name": "read_file",
                            "arguments": '{"path":"README.md"}',
                        }
                    ],
                    "usage": {"input_tokens": 10, "output_tokens": 4},
                }
            return {
                "id": "response-2",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Done"}],
                    }
                ],
            }

        route = resolve_model_route(
            config=AgentFarmConfig(
                model_providers={
                    "local": {
                        "base_url": "http://127.0.0.1:8080/v1",
                        "wire_api": "responses",
                        "requires_openai_auth": False,
                    }
                }
            ),
            repo_root=Path.cwd(),
            provider_id="local",
            model="budget-model",
        )
        self.assertEqual(route.headers["User-Agent"], "AgentFarm/0.4")
        session = ModelSession(
            route=route,
            system_prompt="System",
            timeout_seconds=10,
            transport=transport,
        )
        first = session.send(prompt="Inspect", tools=[])
        second = session.send(
            prompt="Write now",
            tool_results=[{"call_id": "call-1", "output": '{"ok":true}'}],
            tools=[],
        )

        self.assertEqual(first.tool_calls[0].name, "read_file")
        self.assertEqual(second.text, "Done")
        self.assertTrue(
            any(item.get("type") == "function_call_output" for item in requests[1]["input"])
        )
        self.assertEqual(requests[1]["input"][-2]["type"], "function_call_output")
        self.assertEqual(requests[1]["input"][-1]["role"], "user")

    def test_chat_session_uses_standard_tool_messages(self):
        requests = []

        def transport(url, headers, payload, timeout):
            requests.append(payload)
            if len(requests) == 1:
                return {
                    "id": "chat-1",
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "type": "function",
                                        "function": {"name": "list_files", "arguments": "{}"},
                                    }
                                ],
                            }
                        }
                    ],
                }
            return {
                "id": "chat-2",
                "choices": [{"message": {"role": "assistant", "content": "Finished"}}],
            }

        config = AgentFarmConfig(
            model_providers={
                "local": {
                    "base_url": "http://127.0.0.1:1234/v1",
                    "wire_api": "chat",
                    "requires_openai_auth": False,
                }
            }
        )
        route = resolve_model_route(
            config=config,
            repo_root=Path.cwd(),
            provider_id="local",
            model="local-model",
        )
        session = ModelSession(
            route=route,
            system_prompt="System",
            timeout_seconds=10,
            transport=transport,
        )
        session.send(prompt="Inspect", tools=[])
        reply = session.send(
            prompt="Write now",
            tool_results=[{"call_id": "call-1", "output": "ok"}],
            tools=[],
        )

        self.assertEqual(reply.text, "Finished")
        self.assertEqual(requests[1]["messages"][-2]["role"], "tool")
        self.assertEqual(requests[1]["messages"][-1]["role"], "user")

    def test_deepseek_translates_legacy_xhigh_to_max_and_uses_thinking_object(self):
        payload = self._capture_chat_payload(
            self._chat_route("deepseek"),
            reasoning_mode="disabled",
            reasoning_effort="xhigh",
        )
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["reasoning_effort"], "max")

    def test_anthropic_compatibility_does_not_send_ignored_reasoning_effort(self):
        payload = self._capture_chat_payload(
            self._chat_route("anthropic"),
            reasoning_mode="enabled",
            reasoning_effort="high",
        )
        self.assertEqual(payload["thinking"], {"type": "enabled"})
        self.assertNotIn("reasoning_effort", payload)

    def test_qwen_uses_enable_thinking_boolean(self):
        payload = self._capture_chat_payload(
            self._chat_route("qwen"),
            reasoning_mode="disabled",
        )
        self.assertIs(payload["enable_thinking"], False)

    def test_openrouter_uses_reasoning_object(self):
        payload = self._capture_chat_payload(
            self._chat_route("openrouter"),
            reasoning_mode="enabled",
            reasoning_effort="high",
        )
        self.assertEqual(payload["reasoning"], {"enabled": True, "effort": "high"})
        self.assertNotIn("reasoning_effort", payload)

    def test_siliconflow_uses_enable_thinking_and_deepseek_effort(self):
        payload = self._capture_chat_payload(
            self._chat_route("siliconflow"),
            reasoning_mode="enabled",
            reasoning_effort="max",
        )
        self.assertIs(payload["enable_thinking"], True)
        self.assertEqual(payload["reasoning_effort"], "max")

    def test_together_uses_reasoning_toggle_object(self):
        payload = self._capture_chat_payload(
            self._chat_route("together"),
            reasoning_mode="disabled",
        )
        self.assertEqual(payload["reasoning"], {"enabled": False})


if __name__ == "__main__":
    unittest.main()
