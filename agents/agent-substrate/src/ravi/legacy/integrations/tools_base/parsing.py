"""Shared tool-call parsing and lookup helpers.

Used by both assistant-loop execution and runtime tool executor handlers.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from typing import Any, Mapping, MutableMapping, Optional, Sequence
from uuid import uuid4

from ravi.kernel.messages.client_messages import ToolCallMessage


@dataclass(slots=True)
class ParsedToolCall:
    """Normalized representation of a tool call."""

    call_id: str
    name: str
    arguments: dict[str, Any]


def _extract_name_and_args(tc: Any) -> tuple[Optional[str], Any]:
    if hasattr(tc, "function") and isinstance(getattr(tc, "function", None), dict):
        fn = tc.function
        return fn.get("name"), fn.get("arguments")
    if isinstance(tc, dict):
        if "function" in tc and isinstance(tc["function"], dict):
            fn = tc["function"]
            return fn.get("name"), fn.get("arguments")
        return tc.get("name"), tc.get("arguments", {})
    if hasattr(tc, "name") and hasattr(tc, "arguments"):
        return tc.name, tc.arguments
    return None, {}


def parse_tool_call(tc: Any) -> ParsedToolCall:
    """Normalize SDK/tool-call objects into ``ParsedToolCall``."""
    if isinstance(tc, ToolCallMessage):
        return ParsedToolCall(
            call_id=tc.tool_call_id or str(uuid4()),
            name=tc.name,
            arguments=tc.arguments or {},
        )

    call_id: Optional[str] = getattr(tc, "id", None)
    if isinstance(tc, dict):
        call_id = tc.get("id", call_id)

    name, raw_args = _extract_name_and_args(tc)
    if isinstance(raw_args, str):
        try:
            args = json.loads(raw_args)
        except json.JSONDecodeError:
            args = {}
    else:
        args = raw_args

    if not isinstance(args, dict):
        args = {}

    return ParsedToolCall(
        call_id=call_id or str(uuid4()),
        name=name or "unknown",
        arguments=args,
    )


def parse_runtime_tool_payload(payload: Any) -> ParsedToolCall:
    """Parse runtime tool-executor payload into ``ParsedToolCall``.

    Accepts either a dict payload or a single text block that contains dict text.
    """
    candidate = payload
    if isinstance(payload, list) and payload:
        block = payload[0]
        if (
            hasattr(block, "type")
            and getattr(block, "type") == "text"
            and hasattr(block, "text")
        ):
            text = getattr(block, "text")
            for parser in (ast.literal_eval, json.loads):
                try:
                    parsed = parser(text)
                    if isinstance(parsed, dict):
                        candidate = parsed
                        break
                except Exception:
                    continue

    if not isinstance(candidate, dict):
        raise ValueError("Invalid payload: expected dict")

    return ParsedToolCall(
        call_id=str(candidate.get("call_id", "")),
        name=str(candidate.get("tool_name", "")),
        arguments=candidate.get("arguments", {}) if isinstance(candidate.get("arguments", {}), dict) else {},
    )


def find_tool(name: str, tools: Sequence[Any] | Mapping[str, Any], *, catalog: Any = None) -> Any | None:
    """Find tool by name from catalog first, then mapping/list containers."""
    if catalog is not None and hasattr(catalog, "get_tool"):
        catalog_tool = catalog.get_tool(name)
        if catalog_tool is not None:
            return catalog_tool

    if isinstance(tools, Mapping):
        return tools.get(name)

    for tool in tools:
        tool_name = getattr(tool, "name", None)
        if tool_name is None and isinstance(tool, MutableMapping):
            tool_name = tool.get("name")
        if tool_name == name:
            return tool
    return None
