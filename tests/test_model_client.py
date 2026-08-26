import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_farm.model_client import (
    ModelClientError,
    ModelRoute,
    ModelSession,
    ModelTransportError,
    _default_streaming_transport,
    _chat_stream_response,
    _responses_stream_response,
    resolve_model_route,
)
from agent_farm.models import AgentFarmConfig


class ModelClientTests(unittest.TestCase):
    def test_raw_stream_connection_resets_become_retryable_transport_errors(self):
        class ResetResponse:
            headers = {"Content-Type": "text/event-stream"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def __iter__(self):
                raise ConnectionResetError("connection reset by peer")

        with patch("agent_farm.model_client.urlopen", return_value=ResetResponse()):
            with self.assertRaises(ModelTransportError) as raised:
                _default_streaming_transport(
                    "https://example.invalid/chat/completions",
                    {},
                    {"messages": []},
                    None,
                    "chat",
                    lambda event: None,
                )
        self.assertTrue(raised.exception.retryable)

    def test_retries_only_transient_failures_with_one_stable_request_id(self):
        calls = []

        def transport(url, headers, payload, timeout):
            calls.append(headers["X-Client-Request-Id"])
            if len(calls) == 1:
                raise ModelTransportError("temporary", retryable=True, status_code=503)
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

        route = self._chat_route("deepseek")
        route = ModelRoute(**{**route.__dict__, "request_max_retries": 2})
        session = ModelSession(
            route=route, system_prompt="System", timeout_seconds=None, transport=transport
        )
        session._retry_delay = lambda seconds: None
        reply = session.send(prompt="hello")
        self.assertEqual(reply.text, "ok")
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(set(calls)), 1)

    def test_does_not_retry_permanent_or_partially_streamed_failures(self):
        permanent_calls = 0

        def permanent_transport(url, headers, payload, timeout):
            nonlocal permanent_calls
            permanent_calls += 1
            raise ModelTransportError("bad request", retryable=False, status_code=400)

        route = self._chat_route("deepseek")
        route = ModelRoute(**{**route.__dict__, "request_max_retries": 3})
        permanent = ModelSession(
            route=route,
            system_prompt="System",
            timeout_seconds=None,
            transport=permanent_transport,
        )
        permanent._retry_delay = lambda seconds: None
        with self.assertRaises(ModelClientError):
            permanent.send(prompt="hello")
        self.assertEqual(permanent_calls, 1)

        stream_calls = 0

        def streaming_transport(url, headers, payload, timeout, wire_api, callback):
            nonlocal stream_calls
            stream_calls += 1
            callback({"type": "model.output.delta", "delta": "accepted"})
            raise ModelTransportError("connection lost", retryable=True)

        streamed = ModelSession(
            route=route,
            system_prompt="System",
            timeout_seconds=None,
            event_callback=lambda event: None,
            streaming_transport=streaming_transport,
        )
        streamed._retry_delay = lambda seconds: None
        with self.assertRaises(ModelClientError):
            streamed.send(prompt="hello")
        self.assertEqual(stream_calls, 1)

    def test_repeated_pre_output_stream_failures_fall_back_to_non_streaming(self):
        stream_calls = 0
        transport_calls = 0
        events = []

        def streaming_transport(url, headers, payload, timeout, wire_api, callback):
            nonlocal stream_calls
            stream_calls += 1
            callback({"type": "model.reasoning.delta"})
            raise ModelTransportError("stream reset", retryable=True)

        def transport(url, headers, payload, timeout):
            nonlocal transport_calls
            transport_calls += 1
            return {"choices": [{"message": {"role": "assistant", "content": "recovered"}}]}

        route = self._chat_route("deepseek")
        route = ModelRoute(**{**route.__dict__, "request_max_retries": 1})
        session = ModelSession(
            route=route,
            system_prompt="System",
            timeout_seconds=None,
            transport=transport,
            streaming_transport=streaming_transport,
            event_callback=events.append,
        )
        session._retry_delay = lambda seconds: None

        reply = session.send(prompt="hello")

        self.assertEqual(reply.text, "recovered")
        self.assertEqual(stream_calls, 2)
        self.assertEqual(transport_calls, 1)
        self.assertIn("model.request.stream_fallback", [event["type"] for event in events])
        self.assertEqual(session._last_retry_count, 2)

    def test_retry_after_controls_transient_backoff(self):
        delays = []
        calls = 0

        def transport(url, headers, payload, timeout):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ModelTransportError(
                    "rate limited",
                    retryable=True,
                    status_code=429,
                    retry_after_seconds=7.5,
                )
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

        route = self._chat_route("deepseek")
        route = ModelRoute(**{**route.__dict__, "request_max_retries": 1})
        session = ModelSession(
            route=route, system_prompt="System", timeout_seconds=None, transport=transport
        )
        session._retry_delay = delays.append
        session.send(prompt="hello")
        self.assertEqual(delays, [7.5])

    def test_session_can_wait_for_model_without_a_deadline(self):
        observed_timeouts = []

        def transport(url, headers, payload, timeout):
            observed_timeouts.append(timeout)
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

        ModelSession(
            route=self._chat_route("deepseek"),
            system_prompt="System",
            timeout_seconds=None,
            transport=transport,
        ).send(prompt="Think as long as needed")

        self.assertEqual(observed_timeouts, [None])

    def test_responses_session_sends_images_as_multimodal_input(self):
        requests = []

        def transport(url, headers, payload, timeout):
            requests.append(payload)
            return {
                "id": "response-image",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
            }

        route = ModelRoute(
            provider_id="openai",
            template_id="openai",
            model="vision-model",
            base_url="https://example.com/v1",
            wire_api="responses",
            headers={},
            extra_query={},
            request_max_retries=0,
        )
        ModelSession(
            route=route, system_prompt="System", timeout_seconds=5, transport=transport
        ).send(
            prompt="Inspect",
            attachments=[{"name": "chart.png", "data_url": "data:image/png;base64,AAAA"}],
        )

        content = requests[0]["input"][1]["content"]
        self.assertEqual(content[0], {"type": "input_text", "text": "Inspect"})
        self.assertEqual(content[1]["type"], "input_image")
        self.assertEqual(content[1]["image_url"], "data:image/png;base64,AAAA")

    def test_chat_session_sends_images_in_openai_compatible_format(self):
        requests = []

        def transport(url, headers, payload, timeout):
            requests.append(payload)
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

        route = self._chat_route("deepseek")
        ModelSession(
            route=route, system_prompt="System", timeout_seconds=5, transport=transport
        ).send(
            prompt="Inspect",
            attachments=[{"name": "chart.jpg", "data_url": "data:image/jpeg;base64,BBBB"}],
        )

        content = requests[0]["messages"][1]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "Inspect"})
        self.assertEqual(content[1]["type"], "image_url")
        self.assertEqual(content[1]["image_url"]["url"], "data:image/jpeg;base64,BBBB")

    def test_chat_stream_reconstructs_visible_text_and_tool_calls(self):
        emitted = []
        response = _chat_stream_response(
            iter(
                [
                    {"id": "chat-stream", "choices": [{"delta": {"content": "Hel"}}]},
                    {
                        "choices": [
                            {
                                "delta": {
                                    "content": "lo",
                                    "reasoning_content": "hidden reasoning",
                                }
                            }
                        ]
                    },
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call-1",
                                            "function": {"name": "read_", "arguments": '{"path":'},
                                        }
                                    ]
                                }
                            }
                        ]
                    },
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "function": {"name": "file", "arguments": '"README.md"}'},
                                        }
                                    ]
                                }
                            }
                        ]
                    },
                ]
            ),
            emitted.append,
        )

        self.assertEqual(response["choices"][0]["message"]["content"], "Hello")
        function = response["choices"][0]["message"]["tool_calls"][0]["function"]
        self.assertEqual(function["name"], "read_file")
        self.assertEqual(function["arguments"], '{"path":"README.md"}')
        self.assertEqual(
            [event.get("delta") for event in emitted if event["type"] == "model.output.delta"],
            ["Hel", "lo"],
        )
        reasoning_event = next(event for event in emitted if event["type"] == "model.reasoning.delta")
        self.assertNotIn("delta", reasoning_event)

    def test_responses_stream_prefers_completed_response_and_emits_deltas(self):
        emitted = []
        final = {
            "id": "response-stream",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "Hello"}],
                }
            ],
            "usage": {"output_tokens": 2},
        }
        response = _responses_stream_response(
            iter(
                [
                    {"type": "response.output_text.delta", "delta": "Hel"},
                    {"type": "response.output_text.delta", "delta": "lo"},
                    {"type": "response.reasoning_summary_text.delta", "delta": "private"},
                    {"type": "response.completed", "response": final},
                ]
            ),
            emitted.append,
        )

        self.assertIs(response, final)
        self.assertEqual("".join(event.get("delta", "") for event in emitted), "Hello")
        self.assertTrue(any(event["type"] == "model.reasoning.delta" for event in emitted))
        self.assertFalse(any(event.get("delta") == "private" for event in emitted))

    @staticmethod
    def _chat_route(template_id: str, model: str = "test-model") -> ModelRoute:
        return ModelRoute(
            provider_id=template_id,
            template_id=template_id,
            model=model,
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

    def test_custom_compatible_gateway_translates_common_model_families(self):
        openai_payload = self._capture_chat_payload(
            self._chat_route("custom-openai-compatible", "openai/gpt-5.5"),
            reasoning_mode="enabled",
            reasoning_effort="high",
        )
        self.assertEqual(openai_payload["reasoning_effort"], "high")
        self.assertNotIn("thinking", openai_payload)

        claude_payload = self._capture_chat_payload(
            self._chat_route("custom-openai-compatible", "anthropic/claude-sonnet-4.5"),
            reasoning_mode="enabled",
            reasoning_effort="high",
        )
        self.assertEqual(claude_payload["thinking"], {"type": "enabled"})
        self.assertNotIn("reasoning_effort", claude_payload)

        deepseek_payload = self._capture_chat_payload(
            self._chat_route("custom-openai-compatible", "deepseek/deepseek-v4"),
            reasoning_mode="disabled",
            reasoning_effort="xhigh",
        )
        self.assertEqual(deepseek_payload["thinking"], {"type": "disabled"})
        self.assertEqual(deepseek_payload["reasoning_effort"], "max")

        qwen_payload = self._capture_chat_payload(
            self._chat_route("custom-openai-compatible", "qwen/qwen3-235b-a22b"),
            reasoning_mode="enabled",
            reasoning_effort="high",
        )
        self.assertIs(qwen_payload["enable_thinking"], True)
        self.assertNotIn("reasoning_effort", qwen_payload)

        kimi_payload = self._capture_chat_payload(
            self._chat_route("custom-openai-compatible", "moonshotai/kimi-k2-thinking"),
            reasoning_mode="enabled",
            reasoning_effort="high",
        )
        self.assertEqual(kimi_payload["thinking"], {"type": "enabled"})
        self.assertNotIn("reasoning_effort", kimi_payload)

    def test_custom_compatible_responses_gateway_keeps_neutral_controls(self):
        route = ModelRoute(
            provider_id="custom-openai-compatible",
            template_id="custom-openai-compatible",
            model="openai/gpt-5.5",
            base_url="https://example.com/v1",
            wire_api="responses",
            headers={},
            extra_query={},
            request_max_retries=0,
        )
        requests = []

        def transport(url, headers, payload, timeout):
            requests.append(payload)
            return {
                "id": "response-1",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
            }

        ModelSession(
            route=route,
            system_prompt="System",
            timeout_seconds=None,
            reasoning_mode="enabled",
            reasoning_effort="high",
            transport=transport,
        ).send(prompt="Hello")

        self.assertEqual(requests[0]["reasoning"], {"effort": "high"})

    def test_unidentified_gateway_defaults_to_chat_compatibility(self):
        route = resolve_model_route(
            config=AgentFarmConfig(
                model_providers={
                    "relay": {
                        "base_url": "https://relay.example.com/v1",
                        "requires_openai_auth": False,
                    }
                }
            ),
            repo_root=Path.cwd(),
            provider_id="relay",
            model="anthropic/claude-sonnet-4.5",
        )
        self.assertEqual(route.template_id, "custom-openai-compatible")
        self.assertEqual(route.wire_api, "chat")


if __name__ == "__main__":
    unittest.main()
