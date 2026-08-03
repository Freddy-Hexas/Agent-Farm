from __future__ import annotations

import json
import os
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl
from urllib.request import Request, urlopen
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

from .models import AgentFarmConfig
from .provider_templates import provider_template_for
from .usage import PRICE_CATALOG_VERSION, estimate_cost_usd, normalize_usage
from .secrets import load_secrets_env


class ModelClientError(RuntimeError):
    pass


class ModelRequestCancelled(ModelClientError):
    pass


class ModelBudgetExceeded(ModelClientError):
    pass


class ModelTransportError(ModelClientError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ModelReply:
    response_id: str | None
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    raw_output: list[dict[str, Any]] = field(default_factory=list)
    chat_message: dict[str, Any] | None = None


@dataclass(frozen=True)
class ModelRoute:
    provider_id: str
    template_id: str | None
    model: str
    base_url: str
    wire_api: str
    headers: dict[str, str]
    extra_query: dict[str, str]
    request_max_retries: int


Transport = Callable[[str, dict[str, str], dict[str, Any], int | None], dict[str, Any]]
ModelEventCallback = Callable[[dict[str, Any]], None]
StreamingTransport = Callable[
    [str, dict[str, str], dict[str, Any], int | None, str, ModelEventCallback],
    dict[str, Any],
]
BudgetGuard = Callable[[str, str], dict[str, Any]]
UsageRecorder = Callable[[dict[str, Any]], dict[str, Any] | None]
ProviderGuard = Callable[[str, str], dict[str, Any] | None]
ProviderSuccessRecorder = Callable[[str, str], None]
ProviderFailureRecorder = Callable[[str, str, Exception], None]


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


DEFAULT_PROVIDERS: dict[str, dict[str, Any]] = {
    "openai": {
        "template_id": "openai",
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "env_key": "OPENAI_API_KEY",
        "wire_api": "responses",
        "requires_openai_auth": True,
    },
    "ollama": {
        "template_id": "ollama",
        "name": "Ollama",
        "base_url": "http://127.0.0.1:11434/v1",
        "wire_api": "chat",
        "requires_openai_auth": False,
    },
    "lmstudio": {
        "template_id": "lmstudio",
        "name": "LM Studio",
        "base_url": "http://127.0.0.1:1234/v1",
        "wire_api": "chat",
        "requires_openai_auth": False,
    },
}


def _endpoint(base_url: str, wire_api: str) -> str:
    base = base_url.rstrip("/")
    if wire_api == "responses":
        return base if base.endswith("/responses") else base + "/responses"
    return base if base.endswith("/chat/completions") else base + "/chat/completions"


def _with_query(url: str, extra: dict[str, Any]) -> str:
    if not extra:
        return url
    parsed = urlsplit(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update({str(key): str(value) for key, value in extra.items()})
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def _default_transport(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: int | None,
) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")[:16_000]
        raise ModelTransportError(
            f"Model endpoint returned HTTP {exc.code}: {raw}",
            retryable=exc.code in {408, 409, 425, 429, 500, 502, 503, 504},
            status_code=exc.code,
            retry_after_seconds=_retry_after_seconds(exc.headers.get("Retry-After")),
        ) from exc
    except URLError as exc:
        raise ModelTransportError(
            f"Could not reach the model endpoint: {exc.reason}", retryable=True
        ) from exc
    except TimeoutError as exc:
        raise ModelTransportError("The model endpoint timed out.", retryable=True) from exc
    except OSError as exc:
        raise ModelTransportError(
            f"The model connection was interrupted: {exc}", retryable=True
        ) from exc
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ModelClientError("The model endpoint returned invalid JSON.") from exc
    if not isinstance(decoded, dict):
        raise ModelClientError("The model endpoint returned a non-object response.")
    return decoded


def _iter_sse_data(stream: BinaryIO) -> Iterator[str]:
    data_lines: list[str] = []
    for raw_line in stream:
        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            if data_lines:
                yield "\n".join(data_lines)
                data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        yield "\n".join(data_lines)


def _chat_stream_response(
    events: Iterator[dict[str, Any]],
    callback: ModelEventCallback,
) -> dict[str, Any]:
    response_id: str | None = None
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: dict[int, dict[str, Any]] = {}
    usage: dict[str, Any] = {}
    role = "assistant"

    for event in events:
        if isinstance(event.get("error"), dict):
            message = event["error"].get("message") or "The streaming model request failed."
            raise ModelClientError(str(message))
        if event.get("id"):
            response_id = str(event["id"])
        if isinstance(event.get("usage"), dict):
            usage = event["usage"]
        choices = event.get("choices") or []
        if not isinstance(choices, list):
            continue
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta") or {}
            if not isinstance(delta, dict):
                continue
            if delta.get("role"):
                role = str(delta["role"])
            text = delta.get("content")
            if isinstance(text, str) and text:
                content_parts.append(text)
                callback({"type": "model.output.delta", "delta": text})
            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
            if isinstance(reasoning, str) and reasoning:
                reasoning_parts.append(reasoning)
                callback({"type": "model.reasoning.delta"})
            for raw_call in delta.get("tool_calls") or []:
                if not isinstance(raw_call, dict):
                    continue
                index = raw_call.get("index", len(tool_calls))
                if type(index) is not int:
                    index = len(tool_calls)
                call = tool_calls.setdefault(
                    index,
                    {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                )
                if raw_call.get("id"):
                    call["id"] = str(raw_call["id"])
                function = raw_call.get("function") or {}
                if isinstance(function, dict):
                    if function.get("name"):
                        call["function"]["name"] += str(function["name"])
                    if function.get("arguments"):
                        call["function"]["arguments"] += str(function["arguments"])

    message: dict[str, Any] = {"role": role, "content": "".join(content_parts) or None}
    if reasoning_parts:
        message["reasoning_content"] = "".join(reasoning_parts)
    if tool_calls:
        message["tool_calls"] = [tool_calls[index] for index in sorted(tool_calls)]
    return {
        "id": response_id,
        "choices": [{"message": message}],
        "usage": usage,
    }


def _responses_stream_response(
    events: Iterator[dict[str, Any]],
    callback: ModelEventCallback,
) -> dict[str, Any]:
    response_id: str | None = None
    text_parts: list[str] = []
    completed_response: dict[str, Any] | None = None
    usage: dict[str, Any] = {}
    output_items: dict[int, dict[str, Any]] = {}

    for event in events:
        event_type = str(event.get("type") or "")
        if event_type in {"error", "response.failed", "response.incomplete"}:
            error = event.get("error") or (event.get("response") or {}).get("error") or {}
            message = error.get("message") if isinstance(error, dict) else None
            raise ModelClientError(str(message or "The streaming model request failed."))
        response = event.get("response")
        if isinstance(response, dict):
            if response.get("id"):
                response_id = str(response["id"])
            if isinstance(response.get("usage"), dict):
                usage = response["usage"]
            if event_type == "response.completed":
                completed_response = response
        delta = event.get("delta")
        if event_type == "response.output_text.delta" and isinstance(delta, str) and delta:
            text_parts.append(delta)
            callback({"type": "model.output.delta", "delta": delta})
        elif "reasoning" in event_type and event_type.endswith(".delta"):
            callback({"type": "model.reasoning.delta"})
        if event_type == "response.output_item.added" and isinstance(event.get("item"), dict):
            index = event.get("output_index", len(output_items))
            if type(index) is not int:
                index = len(output_items)
            output_items[index] = dict(event["item"])
        elif event_type == "response.function_call_arguments.delta" and isinstance(delta, str):
            index = event.get("output_index", 0)
            if type(index) is not int:
                index = 0
            item = output_items.setdefault(index, {"type": "function_call", "arguments": ""})
            item["arguments"] = str(item.get("arguments") or "") + delta
        elif event_type == "response.output_item.done" and isinstance(event.get("item"), dict):
            index = event.get("output_index", len(output_items))
            if type(index) is not int:
                index = len(output_items)
            output_items[index] = dict(event["item"])

    if completed_response is not None:
        return completed_response
    synthetic_output = [output_items[index] for index in sorted(output_items)]
    if text_parts and not any(item.get("type") == "message" for item in synthetic_output):
        synthetic_output.append(
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "".join(text_parts)}],
            }
        )
    return {"id": response_id, "output": synthetic_output, "usage": usage}


def _default_streaming_transport(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: int | None,
    wire_api: str,
    callback: ModelEventCallback,
) -> dict[str, Any]:
    streaming_payload = deepcopy(payload)
    streaming_payload["stream"] = True
    request_headers = dict(headers)
    request_headers["Accept"] = "text/event-stream"
    request = Request(
        url,
        data=json.dumps(streaming_payload, ensure_ascii=False).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            content_type = response.headers.get("Content-Type", "").casefold()
            if "text/event-stream" not in content_type:
                raw = response.read().decode("utf-8", errors="replace")
                decoded = json.loads(raw)
                if not isinstance(decoded, dict):
                    raise ModelClientError("The model endpoint returned a non-object response.")
                return decoded

            def decoded_events() -> Iterator[dict[str, Any]]:
                for data in _iter_sse_data(response):
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError as exc:
                        raise ModelClientError("The model endpoint returned invalid SSE JSON.") from exc
                    if isinstance(event, dict):
                        yield event

            return (
                _responses_stream_response(decoded_events(), callback)
                if wire_api == "responses"
                else _chat_stream_response(decoded_events(), callback)
            )
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")[:16_000]
        raise ModelTransportError(
            f"Model endpoint returned HTTP {exc.code}: {raw}",
            retryable=exc.code in {408, 409, 425, 429, 500, 502, 503, 504},
            status_code=exc.code,
            retry_after_seconds=_retry_after_seconds(exc.headers.get("Retry-After")),
        ) from exc
    except URLError as exc:
        raise ModelTransportError(
            f"Could not reach the model endpoint: {exc.reason}", retryable=True
        ) from exc
    except TimeoutError as exc:
        raise ModelTransportError("The model endpoint timed out.", retryable=True) from exc
    except OSError as exc:
        raise ModelTransportError(
            f"The model stream was interrupted: {exc}", retryable=True
        ) from exc
    except json.JSONDecodeError as exc:
        raise ModelClientError("The model endpoint returned invalid JSON.") from exc


def resolve_model_route(
    *,
    config: AgentFarmConfig,
    repo_root: Path,
    provider_id: str | None,
    model: str | None,
) -> ModelRoute:
    selected_provider = provider_id or "openai"
    provider = dict(DEFAULT_PROVIDERS.get(selected_provider, {}))
    custom = config.model_providers.get(selected_provider)
    if custom is not None:
        if not isinstance(custom, dict):
            raise ModelClientError(f"Provider '{selected_provider}' has invalid configuration.")
        provider.update(custom)
    if not provider:
        raise ModelClientError(
            f"Provider '{selected_provider}' is not configured. Add it in Settings > Providers."
        )

    selected_model = (model or "").strip()
    if not selected_model:
        raise ModelClientError("A model ID is required for the native agent backend.")
    base_url = str(provider.get("base_url") or "").strip()
    if not base_url:
        raise ModelClientError(f"Provider '{selected_provider}' needs a base URL.")
    wire_api = str(provider.get("wire_api") or "responses").strip().lower()
    if wire_api not in {"responses", "chat"}:
        raise ModelClientError(f"Provider '{selected_provider}' has an unsupported wire API.")

    secret_values = load_secrets_env(repo_root, config.secrets_env)
    # A real User-Agent is not cosmetic: several OpenAI-compatible gateways sit
    # behind bot protection that rejects Python's default urllib identity even
    # when the same endpoint and credential work in a desktop client.
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "AgentFarm/0.4",
    }
    raw_headers = provider.get("http_headers") or {}
    if isinstance(raw_headers, dict):
        headers.update({str(key): str(value) for key, value in raw_headers.items()})
    env_headers = provider.get("env_http_headers") or {}
    if isinstance(env_headers, dict):
        for header, env_key in env_headers.items():
            value = secret_values.get(str(env_key)) or os.environ.get(str(env_key))
            if value:
                headers[str(header)] = value
    env_key = provider.get("env_key")
    api_key = None
    if isinstance(env_key, str) and env_key:
        api_key = secret_values.get(env_key) or os.environ.get(env_key)
    requires_auth = bool(provider.get("requires_openai_auth", bool(env_key)))
    if requires_auth and not api_key:
        raise ModelClientError(
            f"Provider '{selected_provider}' needs the {env_key or 'API key'} credential. "
            "Add it to .agent-farm/secrets.env or the process environment."
        )
    if api_key and "Authorization" not in headers:
        headers["Authorization"] = f"Bearer {api_key}"

    retries = provider.get("request_max_retries", 2)
    if type(retries) is not int:
        retries = 2
    extra_query = provider.get("extra_query") or {}
    if not isinstance(extra_query, dict):
        extra_query = {}
    return ModelRoute(
        provider_id=selected_provider,
        template_id=(provider_template_for(selected_provider, provider) or {}).get("id"),
        model=selected_model,
        base_url=base_url,
        wire_api=wire_api,
        headers=headers,
        extra_query={str(key): str(value) for key, value in extra_query.items()},
        request_max_retries=max(0, retries),
    )


def _tool_payload(tools: list[dict[str, Any]], wire_api: str) -> list[dict[str, Any]]:
    if wire_api == "responses":
        return [
            {
                "type": "function",
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["parameters"],
                "strict": True,
            }
            for tool in tools
        ]
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["parameters"],
            },
        }
        for tool in tools
    ]


def _decode_arguments(raw: Any, name: str) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        raise ModelClientError(f"Tool call '{name}' returned invalid arguments.")
    try:
        decoded = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise ModelClientError(f"Tool call '{name}' returned invalid JSON arguments.") from exc
    if not isinstance(decoded, dict):
        raise ModelClientError(f"Tool call '{name}' arguments must be an object.")
    return decoded


class ModelSession:
    """Small stateful adapter for OpenAI Responses and Chat Completions APIs."""

    def __init__(
        self,
        *,
        route: ModelRoute,
        system_prompt: str,
        timeout_seconds: int | None,
        reasoning_effort: str | None = None,
        reasoning_mode: str | None = None,
        transport: Transport | None = None,
        event_callback: ModelEventCallback | None = None,
        streaming_transport: StreamingTransport | None = None,
        cancel_check: Callable[[], bool] | None = None,
        pricing_overrides: dict[str, dict[str, Any]] | None = None,
        usage_context: dict[str, Any] | None = None,
        budget_guard: BudgetGuard | None = None,
        usage_recorder: UsageRecorder | None = None,
        provider_guard: ProviderGuard | None = None,
        provider_success_recorder: ProviderSuccessRecorder | None = None,
        provider_failure_recorder: ProviderFailureRecorder | None = None,
    ) -> None:
        self.route = route
        self.system_prompt = system_prompt
        self.timeout_seconds = timeout_seconds
        self.reasoning_effort = reasoning_effort
        self.reasoning_mode = reasoning_mode
        self.transport = transport or _default_transport
        self._event_sink = event_callback
        self.cancel_check = cancel_check
        self.event_callback = self._emit_event if event_callback is not None else None
        self.streaming_transport = streaming_transport or _default_streaming_transport
        self._streaming_enabled = event_callback is not None and (
            transport is None or streaming_transport is not None
        )
        self._response_input: list[dict[str, Any]] = []
        self._chat_messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt}
        ]
        self._started = False
        self.pricing_overrides = deepcopy(pricing_overrides or {})
        self.usage_context = deepcopy(usage_context or {})
        self._last_retry_count = 0
        self.budget_guard = budget_guard
        self.usage_recorder = usage_recorder
        self.provider_guard = provider_guard
        self.provider_success_recorder = provider_success_recorder
        self.provider_failure_recorder = provider_failure_recorder
        self._current_request_id: str | None = None
        self._stream_output_started = False

    def _ensure_active(self) -> None:
        if self.cancel_check is not None and self.cancel_check():
            raise ModelRequestCancelled("The model request was cancelled.")

    def _emit_event(self, event: dict[str, Any]) -> None:
        self._ensure_active()
        if self._event_sink is not None:
            self._event_sink(event)

    def _retry_delay(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self._ensure_active()
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))

    def send(
        self,
        *,
        prompt: str | None = None,
        tool_results: list[dict[str, str]] | None = None,
        tools: list[dict[str, Any]] | None = None,
        attachments: list[dict[str, str]] | None = None,
    ) -> ModelReply:
        self._ensure_active()
        request_id = f"model-{uuid.uuid4().hex}"
        request_started = time.monotonic()
        self._last_retry_count = 0
        self._current_request_id = request_id
        self._stream_output_started = False
        if self.provider_guard is not None:
            try:
                provider_health = self.provider_guard(
                    self.route.provider_id, self.route.model
                )
            except Exception as exc:
                if self.event_callback is not None:
                    self.event_callback(
                        {
                            "type": "provider.circuit_open",
                            "request_id": request_id,
                            "provider": self.route.provider_id,
                            "model": self.route.model,
                            "reason": str(exc),
                            **self.usage_context,
                        }
                    )
                raise ModelClientError(str(exc)) from exc
            if provider_health and provider_health.get("status") != "healthy":
                if self.event_callback is not None:
                    self.event_callback(
                        {
                            "type": "provider.health",
                            "request_id": request_id,
                            "provider_health": provider_health,
                            **self.usage_context,
                        }
                    )
        if self.budget_guard is not None:
            try:
                budget = self.budget_guard(self.route.provider_id, self.route.model)
            except Exception as exc:
                if self.event_callback is not None:
                    self.event_callback(
                        {
                            "type": "budget.exceeded",
                            "request_id": request_id,
                            "provider": self.route.provider_id,
                            "model": self.route.model,
                            "reason": str(exc),
                            **self.usage_context,
                        }
                    )
                raise ModelBudgetExceeded(str(exc)) from exc
            if budget.get("status") == "warning" and self.event_callback is not None:
                self.event_callback(
                    {
                        "type": "budget.warning",
                        "request_id": request_id,
                        "budget": budget,
                        **self.usage_context,
                    }
                )
        first_turn = not self._started
        if first_turn:
            if not prompt:
                raise ModelClientError("The first model turn requires a prompt.")
            self._started = True
            response_content: str | list[dict[str, Any]] = prompt
            chat_content: str | list[dict[str, Any]] = prompt
            if attachments:
                response_content = [{"type": "input_text", "text": prompt}]
                chat_content = [{"type": "text", "text": prompt}]
                for attachment in attachments:
                    data_url = attachment.get("data_url")
                    if not data_url:
                        continue
                    response_content.append({"type": "input_image", "image_url": data_url})
                    chat_content.append(
                        {"type": "image_url", "image_url": {"url": data_url}}
                    )
            self._response_input = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": response_content},
            ]
            self._chat_messages.append({"role": "user", "content": chat_content})
        elif attachments:
            raise ModelClientError("Attachments can only be supplied on the first model turn.")

        for result in tool_results or []:
            self._response_input.append(
                {
                    "type": "function_call_output",
                    "call_id": result["call_id"],
                    "output": result["output"],
                }
            )
            self._chat_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": result["call_id"],
                    "content": result["output"],
                }
            )

        # Tool outputs must immediately follow the assistant tool-call message.
        # DeepSeek and other strict Chat Completions providers reject a user
        # message inserted between them. Add any follow-up instruction only
        # after all pending tool results have been recorded.
        if not first_turn and prompt:
            self._response_input.append({"role": "user", "content": prompt})
            self._chat_messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {"model": self.route.model}
        if self.route.wire_api == "responses":
            payload.update({"input": self._response_input, "store": False})
        else:
            payload["messages"] = self._chat_messages
        self._apply_reasoning(payload)
        if tools:
            payload["tools"] = _tool_payload(tools, self.route.wire_api)
            payload["tool_choice"] = "auto"

        if self.event_callback is not None:
            self.event_callback(
                {
                    "type": "model.request.started",
                    "request_id": request_id,
                    "provider": self.route.provider_id,
                    "model": self.route.model,
                    **self.usage_context,
                }
            )
        try:
            response = self._request_stream(payload) if self._streaming_enabled else self._request(payload)
            self._ensure_active()
            reply = (
                self._parse_responses(response)
                if self.route.wire_api == "responses"
                else self._parse_chat(response)
            )
        except (ModelClientError, ModelRequestCancelled, OSError, ValueError) as exc:
            if (
                self.provider_failure_recorder is not None
                and not isinstance(exc, (ModelRequestCancelled, ModelBudgetExceeded))
            ):
                self.provider_failure_recorder(
                    self.route.provider_id, self.route.model, exc
                )
            if self.event_callback is not None:
                self.event_callback(
                    {
                        "type": "model.request.failed",
                        "request_id": request_id,
                        "provider": self.route.provider_id,
                        "model": self.route.model,
                        "latency_ms": round((time.monotonic() - request_started) * 1000, 3),
                        "retry_count": self._last_retry_count,
                        "error": str(exc),
                        **self.usage_context,
                    }
                )
            raise
        normalized_usage = normalize_usage(reply.usage)
        if self.provider_success_recorder is not None:
            self.provider_success_recorder(self.route.provider_id, self.route.model)
        estimated_cost, price_pattern, price = estimate_cost_usd(
            self.route.provider_id,
            self.route.model,
            normalized_usage,
            self.pricing_overrides,
        )
        request_usage: dict[str, Any] = {
            **normalized_usage,
            "estimated_cost_usd": estimated_cost,
            "currency": "USD",
            "price_catalog_version": PRICE_CATALOG_VERSION,
            "price_pattern": price_pattern,
            "price_source": price.get("source") if price else None,
        }
        reply = replace(reply, usage=request_usage)
        if self.route.wire_api == "responses":
            self._response_input.extend(reply.raw_output)
        elif reply.chat_message is not None:
            self._chat_messages.append(reply.chat_message)
        completed_event = {
            "type": "model.request.completed",
            "request_id": request_id,
            "provider": self.route.provider_id,
            "model": self.route.model,
            "latency_ms": round((time.monotonic() - request_started) * 1000, 3),
            "retry_count": self._last_retry_count,
            "usage": reply.usage,
            **self.usage_context,
        }
        post_budget = self.usage_recorder(completed_event) if self.usage_recorder else None
        if self.event_callback is not None:
            self.event_callback(completed_event)
            if post_budget and post_budget.get("status") in {"warning", "denied"}:
                self.event_callback(
                    {
                        "type": (
                            "budget.exceeded"
                            if post_budget.get("status") == "denied"
                            else "budget.warning"
                        ),
                        "request_id": request_id,
                        "budget": post_budget,
                        **self.usage_context,
                    }
                )
        return reply

    def _apply_reasoning(self, payload: dict[str, Any]) -> None:
        """Translate Agent Farm's neutral controls to each provider's wire format."""
        template_id = self.route.template_id
        effort = self.reasoning_effort
        mode = self.reasoning_mode

        # Legacy DeepSeek routes used Codex's xhigh spelling. DeepSeek's API
        # exposes high/max; its docs explicitly map xhigh to max.
        if template_id == "deepseek" and effort == "xhigh":
            effort = "max"

        if template_id == "anthropic":
            if mode:
                payload["thinking"] = {"type": mode}
            return
        if template_id in {"qwen", "siliconflow"}:
            if mode:
                payload["enable_thinking"] = mode == "enabled"
            if template_id == "qwen":
                return
        if template_id in {"kimi", "zhipu", "deepseek", "doubao"}:
            if mode:
                payload["thinking"] = {"type": mode}
            if template_id != "deepseek":
                return
        if template_id == "openrouter":
            reasoning: dict[str, Any] = {}
            if mode:
                reasoning["enabled"] = mode == "enabled"
            if effort:
                reasoning["effort"] = effort
            if reasoning:
                payload["reasoning"] = reasoning
            return
        if template_id == "together":
            if mode:
                payload["reasoning"] = {"enabled": mode == "enabled"}
            if effort:
                payload["reasoning_effort"] = effort
            return
        if template_id == "fireworks" and mode:
            payload["reasoning"] = mode == "enabled"
            return

        if self.route.wire_api == "responses":
            if effort:
                payload["reasoning"] = {"effort": effort}
        elif effort:
            payload["reasoning_effort"] = effort

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = _with_query(_endpoint(self.route.base_url, self.route.wire_api), self.route.extra_query)
        last_error: ModelClientError | None = None
        for attempt in range(self.route.request_max_retries + 1):
            self._ensure_active()
            self._last_retry_count = attempt
            try:
                return self.transport(
                    url,
                    self._request_headers(),
                    deepcopy(payload),
                    self.timeout_seconds,
                )
            except ModelClientError as exc:
                self._ensure_active()
                last_error = exc
                if attempt >= self.route.request_max_retries:
                    raise
                if not isinstance(exc, ModelTransportError) or not exc.retryable:
                    raise
                if self.event_callback is not None:
                    self.event_callback(
                        {"type": "model.request.retrying", "attempt": attempt + 2}
                    )
                delay = exc.retry_after_seconds
                self._retry_delay(delay if delay is not None else min(2**attempt, 4))
        raise last_error or ModelClientError("Model request failed.")

    def _request_stream(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = _with_query(_endpoint(self.route.base_url, self.route.wire_api), self.route.extra_query)
        last_error: ModelClientError | None = None
        callback = self.event_callback or (lambda event: None)
        def tracked_callback(event: dict[str, Any]) -> None:
            if event.get("type") == "model.output.delta":
                self._stream_output_started = True
            callback(event)
        for attempt in range(self.route.request_max_retries + 1):
            self._ensure_active()
            self._last_retry_count = attempt
            try:
                return self.streaming_transport(
                    url,
                    self._request_headers(),
                    deepcopy(payload),
                    self.timeout_seconds,
                    self.route.wire_api,
                    tracked_callback,
                )
            except ModelClientError as exc:
                self._ensure_active()
                last_error = exc
                if attempt >= self.route.request_max_retries:
                    if (
                        isinstance(exc, ModelTransportError)
                        and exc.retryable
                        and not self._stream_output_started
                    ):
                        callback(
                            {
                                "type": "model.request.stream_fallback",
                                "stream_attempts": attempt + 1,
                            }
                        )
                        response = self._request(payload)
                        self._last_retry_count += attempt + 1
                        return response
                    raise
                if (
                    not isinstance(exc, ModelTransportError)
                    or not exc.retryable
                    or self._stream_output_started
                ):
                    raise
                callback({"type": "model.request.retrying", "attempt": attempt + 2})
                delay = exc.retry_after_seconds
                self._retry_delay(delay if delay is not None else min(2**attempt, 4))
        raise last_error or ModelClientError("Streaming model request failed.")

    def _request_headers(self) -> dict[str, str]:
        headers = dict(self.route.headers)
        if self._current_request_id:
            headers.setdefault("X-Client-Request-Id", self._current_request_id)
        return headers

    @staticmethod
    def _parse_responses(response: dict[str, Any]) -> ModelReply:
        output = response.get("output") or []
        if not isinstance(output, list):
            raise ModelClientError("Responses API output must be an array.")
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        raw_output = [item for item in output if isinstance(item, dict)]
        for item in raw_output:
            item_type = item.get("type")
            if item_type == "function_call":
                name = str(item.get("name") or "")
                calls.append(
                    ToolCall(
                        call_id=str(item.get("call_id") or item.get("id") or ""),
                        name=name,
                        arguments=_decode_arguments(item.get("arguments", "{}"), name),
                    )
                )
            elif item_type == "message":
                content = item.get("content") or []
                if isinstance(content, str):
                    text_parts.append(content)
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") in {"output_text", "text"}:
                            text_parts.append(str(part.get("text") or ""))
        if not text_parts and isinstance(response.get("output_text"), str):
            text_parts.append(response["output_text"])
        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        return ModelReply(
            response_id=str(response.get("id")) if response.get("id") else None,
            text="\n".join(part for part in text_parts if part).strip(),
            tool_calls=calls,
            usage=usage,
            raw_output=raw_output,
        )

    @staticmethod
    def _parse_chat(response: dict[str, Any]) -> ModelReply:
        choices = response.get("choices") or []
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ModelClientError("Chat Completions response did not contain a choice.")
        raw_message = choices[0].get("message")
        if not isinstance(raw_message, dict):
            raise ModelClientError("Chat Completions response did not contain a message.")
        content = raw_message.get("content") or ""
        if isinstance(content, list):
            text_value = "\n".join(
                str(part.get("text") or "") for part in content if isinstance(part, dict)
            )
        else:
            text_value = str(content)
        calls: list[ToolCall] = []
        for raw_call in raw_message.get("tool_calls") or []:
            if not isinstance(raw_call, dict):
                continue
            function = raw_call.get("function") or {}
            if not isinstance(function, dict):
                continue
            name = str(function.get("name") or "")
            calls.append(
                ToolCall(
                    call_id=str(raw_call.get("id") or ""),
                    name=name,
                    arguments=_decode_arguments(function.get("arguments", "{}"), name),
                )
            )
        assistant_message = {
            key: raw_message[key]
            for key in ("role", "content", "tool_calls", "reasoning_content")
            if key in raw_message
        }
        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        return ModelReply(
            response_id=str(response.get("id")) if response.get("id") else None,
            text=text_value.strip(),
            tool_calls=calls,
            usage=usage,
            chat_message=assistant_message,
        )
