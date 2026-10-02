"""The Anthropic client, held to the LLM-client conformance suite through the real SDK over a scripted transport."""

from __future__ import annotations

import json

import httpx
import pytest
from anthropic import AsyncAnthropic

from substrate.integrations.llm.anthropic.anthropic_client import AnthropicClient
from substrate.models import ModelCapabilities
from substrate.testing.conformance.chat_model import ChatModelConformance

CAPS = ModelCapabilities(model_id="conformance-claude", input_modalities=frozenset({"text", "image"}))


def _message(text: str, stop: str) -> dict:
    return {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "conformance-claude",
        "content": [{"type": "text", "text": text}], "stop_reason": stop, "stop_sequence": None,
        "usage": {"input_tokens": 5, "output_tokens": 3},
    }


def _sse(*events: tuple[str, dict]) -> bytes:
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events).encode()


class Messages:
    def client(self, handler, *, capabilities=None):
        client = AnthropicClient(model="conformance-claude", api_key="k", capabilities=capabilities or CAPS, max_tokens=64)

        def routed(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/count_tokens"):  # a different endpoint with its own response shape
                return httpx.Response(200, json={"input_tokens": 7})
            return handler(request)

        client.client = AsyncAnthropic(api_key="k", max_retries=0, http_client=httpx.AsyncClient(transport=httpx.MockTransport(routed)))
        return client

    def text_response(self, text):
        return httpx.Response(200, json=_message(text, "end_turn"))

    def truncated_response(self, text):
        return httpx.Response(200, json=_message(text, "max_tokens"))

    def stream_response(self, text):
        half = len(text) // 2
        body = _sse(
            ("message_start", {"type": "message_start", "message": {**_message("", "end_turn"), "content": [], "stop_reason": None, "usage": {"input_tokens": 5, "output_tokens": 0}}}),
            ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text[:half]}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text[half:]}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 3}}),
            ("message_stop", {"type": "message_stop"}),
        )
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    def error_response(self, status, message, *, retry_after=None):
        headers = {"retry-after": retry_after} if retry_after else {}
        kind = {401: "authentication_error", 429: "rate_limit_error", 400: "invalid_request_error"}.get(status, "api_error")
        return httpx.Response(status, json={"type": "error", "error": {"type": kind, "message": message}}, headers=headers)


class TestAnthropicClient(ChatModelConformance):
    @pytest.fixture
    def provider(self):
        return Messages()
