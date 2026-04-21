from __future__ import annotations

from ravi.core.agents.react_agent import _parse_textual_tool_call_sequence


def test_parse_textual_tool_call_sequence_supports_self_closing_groq_format() -> None:
    parsed = _parse_textual_tool_call_sequence(
        '<function/google_workspace {"service": "gmail", "query": ""}/>'
    )

    assert len(parsed) == 1
    assert parsed[0].name == "google_workspace"
    assert parsed[0].arguments == {"service": "gmail", "query": ""}


def test_parse_textual_tool_call_sequence_supports_explicit_closing_tag() -> None:
    parsed = _parse_textual_tool_call_sequence(
        '<function/google_workspace{"service": "gmail", "query": ""}></function>'
    )

    assert len(parsed) == 1
    assert parsed[0].name == "google_workspace"
    assert parsed[0].arguments == {"service": "gmail", "query": ""}
