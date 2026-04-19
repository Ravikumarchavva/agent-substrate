from __future__ import annotations

from raavan.core.messages.encoders.openai import encode_tools, ensure_strict_tool_schema
from google.genai import types as genai_types
from raavan.integrations.llm.anthropic.anthropic_client import AnthropicClient
from raavan.integrations.llm.gemini.gemini_client import GeminiClient
from raavan.integrations.mcp.app_tools import JsonExplorerTool
from raavan.integrations.llm.openai.openai_chat_client import (
    OpenAIChatCompletionClient,
)
from raavan.integrations.llm.openai.openai_client import OpenAIClient


def _sample_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "service": {
                "type": "string",
                "enum": ["drive", "calendar", "gmail"],
            },
            "query": {
                "type": "string",
            },
            "filters": {
                "type": "object",
                "properties": {
                    "from": {"type": "string"},
                    "unread": {"type": "boolean"},
                },
            },
        },
        "required": ["service"],
    }


def test_ensure_strict_tool_schema_makes_optional_fields_nullable() -> None:
    schema = ensure_strict_tool_schema(_sample_schema())

    assert schema["additionalProperties"] is False
    assert schema["required"] == ["service", "query", "filters"]

    query_schema = schema["properties"]["query"]
    assert query_schema["type"] == ["string", "null"]

    filters_schema = schema["properties"]["filters"]
    assert filters_schema["type"] == ["object", "null"]
    assert filters_schema["additionalProperties"] is False
    assert filters_schema["required"] == ["from", "unread"]
    assert filters_schema["properties"]["from"]["type"] == ["string", "null"]
    assert filters_schema["properties"]["unread"]["type"] == ["boolean", "null"]


def test_encode_tools_enforces_strict_schema_and_flag() -> None:
    tools = [
        {
            "name": "google_workspace",
            "description": "Read Gmail, Calendar, or Drive data.",
            "inputSchema": _sample_schema(),
        }
    ]

    encoded = encode_tools(tools)

    assert encoded is not None
    assert len(encoded) == 1
    assert encoded[0]["name"] == "google_workspace"
    assert encoded[0]["strict"] is True
    assert encoded[0]["parameters"]["required"] == ["service", "query", "filters"]
    assert encoded[0]["parameters"]["properties"]["query"]["type"] == [
        "string",
        "null",
    ]


def test_chat_completions_tool_serialization_matches_strict_rules() -> None:
    tools = [
        {
            "name": "google_workspace",
            "description": "Read Gmail, Calendar, or Drive data.",
            "inputSchema": _sample_schema(),
        }
    ]

    # _serialize_tools_chat is an instance method that checks self.provider
    client = OpenAIChatCompletionClient(model="test", api_key="test-key")
    client.provider = "openai"  # type: ignore[attr-defined]
    encoded = client._serialize_tools_chat(tools)

    assert encoded is not None
    assert len(encoded) == 1
    fn = encoded[0]["function"]
    assert fn["strict"] is True
    assert fn["parameters"]["required"] == ["service", "query", "filters"]
    assert fn["parameters"]["properties"]["filters"]["type"] == [
        "object",
        "null",
    ]


def test_chat_completions_tool_serialization_skips_strict_for_groq() -> None:
    tools = [
        {
            "name": "google_workspace",
            "description": "Read Gmail, Calendar, or Drive data.",
            "inputSchema": _sample_schema(),
        }
    ]

    client = OpenAIChatCompletionClient(model="test", api_key="test-key")
    client.provider = "groq"  # type: ignore[attr-defined]
    encoded = client._serialize_tools_chat(tools)

    assert encoded is not None
    assert len(encoded) == 1
    fn = encoded[0]["function"]
    assert "strict" not in fn
    # Original schema preserved — not transformed by ensure_strict_tool_schema
    assert fn["parameters"]["required"] == ["service"]


def test_json_explorer_schema_remains_valid_under_openai_strict_mode() -> None:
    schema = ensure_strict_tool_schema(
        JsonExplorerTool().get_openai_schema()["function"]["parameters"]
    )

    assert schema["required"] == ["title", "data"]
    assert schema["properties"]["data"]["type"] == "string"


def test_openai_responses_named_tool_choice_is_normalized() -> None:
    client = OpenAIClient(model="test", api_key="test-key")

    assert client._normalize_tool_choice("google_workspace") == {
        "type": "function",
        "name": "google_workspace",
    }


def test_chat_completions_named_tool_choice_is_normalized() -> None:
    client = OpenAIChatCompletionClient(model="test", api_key="test-key")

    assert client._normalize_chat_tool_choice("google_workspace") == {
        "type": "function",
        "function": {"name": "google_workspace"},
    }


def test_anthropic_named_tool_choice_is_normalized() -> None:
    client = AnthropicClient(model="test", api_key="test-key")

    assert client._normalize_tool_choice("google_workspace") == {
        "type": "tool",
        "name": "google_workspace",
    }


def test_gemini_named_tool_choice_is_normalized() -> None:
    client = GeminiClient(model="test", api_key="test-key")

    tool_config = client._build_tool_config("google_workspace")

    assert tool_config is not None
    assert tool_config.function_calling_config is not None
    assert (
        tool_config.function_calling_config.mode
        == genai_types.FunctionCallingConfigMode.ANY
    )
    assert tool_config.function_calling_config.allowed_function_names == [
        "google_workspace"
    ]
