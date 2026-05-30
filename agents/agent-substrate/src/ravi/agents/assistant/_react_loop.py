"""ReAct loop helpers — pure functions for message manipulation and tool-call parsing.

These functions have no dependency on the ReActAgent class and can be tested
and reasoned about independently.
"""

from __future__ import annotations

import json
from typing import List, Optional

from ravi.agents.assistant._legacy_stubs import (
    AssistantMessage,
    UserMessage,
)

# Deleted types — kept as Any for body compatibility
BaseClientMessage = object
ToolCallMessage = object
MediaType = object


# ---------------------------------------------------------------------------
# User message helpers
# ---------------------------------------------------------------------------


def resolve_user_message_content(
    input_text: str,
    input_content: Optional[list[MediaType]],
) -> list[MediaType]:
    """Return content list for the user message, falling back to plain text."""
    if input_content is None:
        return [input_text]
    if not isinstance(input_content, list):
        raise ValueError("input_content must be a list of media items")
    if input_content:
        return input_content
    return [input_text]


def has_non_text_media(content: list[MediaType]) -> bool:
    return any(not isinstance(item, str) for item in content)


def build_persisted_user_message(
    input_text: str,
    content: list[MediaType],
) -> UserMessage:
    """Build the UserMessage stored in memory (strips non-text media)."""
    if has_non_text_media(content):
        return UserMessage(content=[input_text])
    return UserMessage(content=content)


def inject_ephemeral_user_message(
    raw_messages: list[BaseClientMessage],
    input_text: str,
    ephemeral_content: list[MediaType],
) -> list[BaseClientMessage]:
    """Replace the matching text-only UserMessage with a multimodal version for LLM calls."""
    if not has_non_text_media(ephemeral_content):
        return raw_messages

    expected_text = input_text.strip()
    patched_messages = list(raw_messages)

    for index in range(len(patched_messages) - 1, -1, -1):
        candidate = patched_messages[index]
        if not isinstance(candidate, UserMessage):
            continue
        if any(not isinstance(item, str) for item in candidate.content):
            continue

        text_parts = [
            item for item in candidate.content if isinstance(item, str) and item
        ]
        candidate_text = "\n".join(text_parts).strip()
        if candidate_text != expected_text:
            continue

        patched_messages[index] = UserMessage(
            content=ephemeral_content,
            name=candidate.name,
        )
        break

    return patched_messages


def sanitize_message_for_model_context(
    message: BaseClientMessage,
) -> BaseClientMessage:
    """Strip non-text media from a UserMessage before storing in model context."""
    if not isinstance(message, UserMessage):
        return message
    if not has_non_text_media(message.content):
        return message

    text_parts = [
        item for item in message.content if isinstance(item, str) and item.strip()
    ]
    sanitized_text = "\n".join(text_parts).strip()
    if not sanitized_text:
        sanitized_text = "[User provided a non-text attachment in a previous turn.]"

    return UserMessage(content=[sanitized_text], name=message.name)


def prepare_model_context_messages(
    raw_messages: list[BaseClientMessage],
    input_text: str,
    ephemeral_content: list[MediaType],
) -> list[BaseClientMessage]:
    """Sanitize messages for model context storage and inject ephemeral media."""
    sanitized_messages = [
        sanitize_message_for_model_context(message) for message in raw_messages
    ]
    return inject_ephemeral_user_message(
        sanitized_messages,
        input_text,
        ephemeral_content,
    )


# ---------------------------------------------------------------------------
# Assistant message helpers
# ---------------------------------------------------------------------------


def assistant_text_parts(content: Optional[List[MediaType]]) -> list[str]:
    """Extract all text strings from AssistantMessage content."""
    parts: list[str] = []
    if not content:
        return parts

    for item in content:
        if isinstance(item, str):
            if item:
                parts.append(item)
            continue
        if isinstance(item, dict):
            text = item.get("text")
            if isinstance(text, str) and text:
                parts.append(text)

    return parts


def extract_text(response: AssistantMessage) -> Optional[str]:
    """Extract plain text content from an AssistantMessage, or None."""
    if response.content is None:
        return None
    if isinstance(response.content, list):
        parts = assistant_text_parts(response.content)
        return " ".join(parts) if parts else None
    return str(response.content) if response.content else None


def normalize_textual_tool_calls(response: AssistantMessage) -> AssistantMessage:
    """Translate fallback textual tool-call markup into ToolCallMessage objects.

    Some LLM providers (e.g. Groq) emit tool calls as text like:
    ``<function=tool_name{"arg": "value"}</function>``
    instead of structured tool_calls.  This function detects and converts them.
    """
    if response.tool_calls:
        return response

    text = extract_text(response)
    if not text:
        return response

    parsed_calls = parse_textual_tool_call_sequence(text)
    if not parsed_calls:
        return response

    response.tool_calls = parsed_calls
    response.content = None
    response.finish_reason = "tool_calls"
    return response


# ---------------------------------------------------------------------------
# Textual tool call parsing (Groq / legacy format)
# ---------------------------------------------------------------------------


def parse_textual_tool_call_sequence(text: str) -> list[ToolCallMessage]:
    """Parse ``<function=name{...}</function>`` or ``<function/name{...}/>`` sequences."""
    remaining = text.strip()
    parsed_calls: list[ToolCallMessage] = []

    while remaining:
        if remaining.startswith("<function="):
            prefix_len = len("<function=")
        elif remaining.startswith("<function/"):
            prefix_len = len("<function/")
        else:
            return []

        close_tag_index = remaining.find("</function>")
        self_closing_index = remaining.find("/>")

        if close_tag_index == -1 and self_closing_index == -1:
            return []

        is_self_closing = self_closing_index != -1 and (
            close_tag_index == -1 or self_closing_index < close_tag_index
        )
        end_index = self_closing_index if is_self_closing else close_tag_index

        inner = remaining[prefix_len:end_index].strip()

        tool_name = ""
        raw_arguments = ""

        open_brace_index = inner.find("{")
        if open_brace_index > 0:
            tool_name = inner[:open_brace_index].strip().rstrip(">")
            raw_arguments = inner[open_brace_index:].strip()
        else:
            comma_index = inner.find(",")
            if comma_index <= 0:
                return []
            tool_name = inner[:comma_index].strip()
            raw_arguments = inner[comma_index + 1 :].strip()

        if not tool_name or not raw_arguments:
            return []

        if raw_arguments.endswith(">"):
            raw_arguments = raw_arguments[:-1].strip()

        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError:
            return []

        if not isinstance(arguments, dict):
            return []

        parsed_calls.append(ToolCallMessage(name=tool_name, arguments=arguments))
        closing_len = len("/>") if is_self_closing else len("</function>")
        remaining = remaining[end_index + closing_len :].strip()

    return parsed_calls
