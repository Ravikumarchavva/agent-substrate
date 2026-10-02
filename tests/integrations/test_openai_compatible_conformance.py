"""The OpenAI-compatible chat client, held to the LLM-client conformance suite — through the real
OpenAI SDK over a scripted HTTP transport."""

from __future__ import annotations

import json

import httpx
import pytest

from substrate.integrations.llm.openai_compatible import OpenAICompatibleClient
from substrate.models import ModelCapabilities
from substrate.testing.conformance.llm_client import LLMClientConformance

CAPS = ModelCapabilities(model_id="conformance-model", input_modalities=frozenset({"text", "image"}))


def _json(status: int, body: dict, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(status, json=body, headers=headers or {})


def _completion(text: str, finish: str) -> dict:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "conformance-model",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": finish}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }


class ChatCompletions:
    def client(self, handler, *, capabilities=None):
        return _build(handler, capabilities or CAPS)

    def text_response(self, text):
        return _json(200, _completion(text, "stop"))

    def truncated_response(self, text):
        return _json(200, _completion(text, "length"))

    def stream_response(self, text):
        def chunk(delta: dict, finish: str | None = None, usage: dict | None = None) -> str:
            body = {"id": "c", "object": "chat.completion.chunk", "created": 0, "model": "conformance-model",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}] if delta is not None else []}
            if usage:
                body["usage"] = usage
            return f"data: {json.dumps(body)}\n\n"

        half = len(text) // 2
        sse = (
            chunk({"role": "assistant", "content": text[:half]})
            + chunk({"content": text[half:]})
            + chunk({}, "stop")
            + chunk(None, usage={"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8})  # type: ignore[arg-type]
            + "data: [DONE]\n\n"
        )
        return httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"})

    def error_response(self, status, message, *, retry_after=None):
        headers = {"retry-after": retry_after} if retry_after else {}
        return _json(status, {"error": {"message": message, "type": "error", "code": None}}, headers)


def _build(handler, capabilities):
    client = OpenAICompatibleClient(
        model="conformance-model",
        api_key="k",
        base_url="http://provider.invalid/v1",
        capabilities=capabilities,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    client.client.max_retries = 0  # the engine retries; the SDK's own retry would hide the first failure
    return client


class TestOpenAICompatibleClient(LLMClientConformance):
    @pytest.fixture
    def provider(self):
        return ChatCompletions()
