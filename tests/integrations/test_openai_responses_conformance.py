"""The OpenAI (Responses API) client, held to the LLM-client conformance suite through the real SDK
over a scripted transport."""

from __future__ import annotations

import json

import httpx
import pytest
from openai import AsyncOpenAI

from substrate.integrations.llm.openai.openai_client import OpenAIClient
from substrate.models import ModelCapabilities
from substrate.testing.conformance.llm_client import LLMClientConformance

CAPS = ModelCapabilities(model_id="conformance-gpt", input_modalities=frozenset({"text", "image"}))


def _response(text: str, *, status: str = "completed", incomplete: dict | None = None) -> dict:
    return {
        "id": "resp_1", "object": "response", "created_at": 0, "model": "conformance-gpt", "status": status,
        "incomplete_details": incomplete, "error": None, "instructions": None, "metadata": {},
        "output": [{"id": "msg_1", "type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": text, "annotations": []}]}],
        "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
        "usage": {"input_tokens": 5, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 3,
                  "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 8},
    }


class Responses:
    def client(self, handler, *, capabilities=None):
        client = OpenAIClient(model="conformance-gpt", api_key="k", capabilities=capabilities or CAPS)
        client.client = AsyncOpenAI(api_key="k", max_retries=0, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        return client

    def text_response(self, text):
        return httpx.Response(200, json=_response(text))

    def truncated_response(self, text):
        return httpx.Response(200, json=_response(text, status="incomplete", incomplete={"reason": "max_output_tokens"}))

    def stream_response(self, text):
        half = len(text) // 2
        final = _response(text)

        def event(name: str, data: dict) -> str:
            return f"event: {name}\ndata: {json.dumps({'type': name, **data})}\n\n"

        item = {"id": "msg_1", "type": "message", "role": "assistant", "status": "in_progress", "content": []}
        body = (
            event("response.created", {"response": {**final, "status": "in_progress", "output": []}, "sequence_number": 0})
            + event("response.output_item.added", {"output_index": 0, "item": item, "sequence_number": 1})
            + event("response.content_part.added", {"item_id": "msg_1", "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": "", "annotations": []}, "sequence_number": 2})
            + event("response.output_text.delta", {"item_id": "msg_1", "output_index": 0, "content_index": 0, "delta": text[:half], "sequence_number": 3})
            + event("response.output_text.delta", {"item_id": "msg_1", "output_index": 0, "content_index": 0, "delta": text[half:], "sequence_number": 4})
            + event("response.completed", {"response": final, "sequence_number": 5})
        )
        return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})

    def error_response(self, status, message, *, retry_after=None):
        headers = {"retry-after": retry_after} if retry_after else {}
        return httpx.Response(status, json={"error": {"message": message, "type": "error", "code": None, "param": None}}, headers=headers)


class TestOpenAIClient(LLMClientConformance):
    @pytest.fixture
    def provider(self):
        return Responses()
