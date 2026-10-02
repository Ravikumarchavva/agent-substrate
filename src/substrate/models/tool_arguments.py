"""Parsing of a model's tool-call arguments."""

from __future__ import annotations

import json
from typing import Any


def parse_tool_arguments(raw: Any) -> tuple[dict[str, Any], str | None]:
    """Parse a model's tool-call arguments. Malformed JSON — common from
    smaller/local models — becomes an error to report back to the model,
    not an exception that kills the run."""
    if isinstance(raw, dict):
        return raw, None
    if not raw:
        return {}, None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, f"arguments were not valid JSON ({exc.msg}): {raw[:500]}"
    if not isinstance(parsed, dict):
        return {}, f"arguments must be a JSON object, got: {raw[:500]}"
    return parsed, None


__all__ = ["parse_tool_arguments"]
