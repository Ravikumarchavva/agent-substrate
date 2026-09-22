"""OpenAIChatCompletionClient — the client every self-hosted server (vLLM,
SGLang, llama.cpp's llama-server) actually gets routed through by
LLMFactory (see tests/integrations/test_llm_factory_routing.py). No real
server involved: an httpx.MockTransport injected via AsyncOpenAI's own
``http_client=`` seam fakes the wire, matching this repo's established
pattern for testing OpenAI-SDK-shaped clients (see
tests/embedding_reranker/test_embedding.py for the sibling pattern one
layer down).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from substrate.agents.llm.chat_client import OpenAIChatCompletionClient
from substrate.kernel import ChatMessage, TextBlock
from substrate.kernel.llm import GenerationOptions


def _client(handler, **kwargs) -> OpenAIChatCompletionClient:
    return OpenAIChatCompletionClient(
        model="qwen3.5-0.8b",
        api_key="local",
        base_url="http://localhost:8080/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


def _user_message(text: str) -> list[ChatMessage]:
    return [ChatMessage(role="user", content=[TextBlock(text=text)])]


@dataclass
class _StubTool:
    """Minimal stand-in satisfying the kernel `Tool` Protocol's shape
    (`.name`/`.description`/`.input_schema`) -- _tools_to_dicts reads
    exactly these three attributes, nothing else."""

    name: str
    description: str
    input_schema: dict[str, Any]


async def test_generate_hits_chat_completions_not_responses() -> None:
    """The one thing that mattered in the whole audit: this must be a
    request to /v1/chat/completions, the endpoint every self-hosted server
    actually implements -- not /v1/responses, which none of them do."""
    seen_path = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_path["path"] = request.url.path
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1},
            },
        )

    client = _client(handler)
    response = await client.generate(_user_message("hello"))

    assert seen_path["path"] == "/v1/chat/completions"
    assert response.content[0].text == "hi"
    assert response.usage.input_tokens == 3
    assert response.usage.output_tokens == 1


async def test_generate_sends_tool_schema_and_parses_tool_call_back() -> None:
    """Round-trips a tool call through the wire format a self-hosted
    server actually returns -- this is the path ReActAgent's tool loop
    depends on, so it can't just be "the text works"."""
    sent_body = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent_body.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "get_weather",
                                        "arguments": '{"city": "Delhi"}',
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    client = _client(handler)
    tools = [
        _StubTool(
            name="get_weather",
            description="Get the weather",
            input_schema={
                "type": "object",
                "properties": {"city": {"type": "string"}},
            },
        )
    ]
    response = await client.generate(
        _user_message("weather in Delhi?"),
        options=GenerationOptions(tools=tools),
    )

    sent_tool = sent_body["tools"][0]["function"]
    assert sent_tool["name"] == "get_weather"
    # A non-"openai" provider must never get strict:true forced on -- most
    # self-hosted servers reject or ignore fields tied to OpenAI's own
    # strict-schema mode, and this client defaults provider to "compatible"
    # whenever a base_url is set (see __init__).
    assert "strict" not in sent_tool

    tool_use = response.content[0]
    assert tool_use.tool_name == "get_weather"
    assert tool_use.arguments == {"city": "Delhi"}
    assert tool_use.call_id == "call_1"


async def test_generate_stream_yields_text_deltas_then_completion() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        chunks = [
            {"choices": [{"delta": {"content": "Hel"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]},
        ]
        body = (
            "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        )
        return httpx.Response(
            200, content=body, headers={"content-type": "text/event-stream"}
        )

    client = _client(handler)
    deltas = []
    async for event in client.generate_stream(_user_message("hi")):
        deltas.append(event)

    from substrate.kernel.messaging.stream import CompletionEvent, TextDelta

    text_deltas = [e for e in deltas if isinstance(e, TextDelta)]
    completions = [e for e in deltas if isinstance(e, CompletionEvent)]
    assert "".join(d.text for d in text_deltas) == "Hello"
    assert completions[0].content[0].text == "Hello"


async def test_generate_raises_a_clear_error_on_http_failure() -> None:
    """A self-hosted server that's down or misconfigured must fail with a
    message that says so, not an opaque SDK traceback -- this is the
    failure mode someone hits first when pointing this at their own
    llama.cpp/vLLM/SGLang instance."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "model not loaded"}})

    client = _client(handler)
    with pytest.raises(RuntimeError, match="model not loaded"):
        await client.generate(_user_message("hi"))
