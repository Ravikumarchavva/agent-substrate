"""The Gemini client, held to the LLM-client conformance suite through the real google-genai SDK over a
scripted transport."""

from __future__ import annotations

import json

import httpx
import pytest
from google import genai
from google.genai import types as genai_types

from substrate.integrations.llm.gemini.gemini_client import GeminiClient
from substrate.kernel.abstractions.llm import ModelCapabilities
from substrate.kernel.testing.conformance.llm_client import LLMClientConformance

CAPS = ModelCapabilities(model_id="conformance-gemini", input_modalities=frozenset({"text", "image"}))


def _candidate(text: str, finish: str) -> dict:
    return {
        "candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": finish, "index": 0}],
        "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3, "totalTokenCount": 8},
        "modelVersion": "conformance-gemini",
        "responseId": "r1",
    }


class GenerateContent:
    def client(self, handler, *, capabilities=None):
        client = GeminiClient(model="conformance-gemini", api_key="k", capabilities=capabilities or CAPS)

        def routed(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith(":countTokens"):
                return httpx.Response(200, json={"totalTokens": 7})
            return handler(request)

        client.client = genai.Client(
            api_key="k",
            http_options=genai_types.HttpOptions(httpx_async_client=httpx.AsyncClient(transport=httpx.MockTransport(routed))),
        )
        return client

    def text_response(self, text):
        return httpx.Response(200, json=_candidate(text, "STOP"))

    def truncated_response(self, text):
        return httpx.Response(200, json=_candidate(text, "MAX_TOKENS"))

    def stream_response(self, text):
        half = len(text) // 2
        events = [
            {**_candidate(text[:half], "STOP"), "candidates": [{"content": {"role": "model", "parts": [{"text": text[:half]}]}, "index": 0}]},
            _candidate(text[half:], "STOP"),
        ]
        body = "".join(f"data: {json.dumps(e)}\r\n\r\n" for e in events)
        return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})

    def error_response(self, status, message, *, retry_after=None):
        headers = {"retry-after": retry_after} if retry_after else {}
        names = {400: "INVALID_ARGUMENT", 401: "UNAUTHENTICATED", 429: "RESOURCE_EXHAUSTED", 503: "UNAVAILABLE"}
        return httpx.Response(status, json={"error": {"code": status, "message": message, "status": names.get(status, "UNKNOWN")}}, headers=headers)


class TestGeminiClient(LLMClientConformance):
    @pytest.fixture
    def provider(self):
        return GenerateContent()
