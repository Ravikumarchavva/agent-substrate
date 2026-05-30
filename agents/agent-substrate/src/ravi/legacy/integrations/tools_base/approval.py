"""Shared tool approval helpers."""

from __future__ import annotations

from typing import Iterable


def tool_needs_approval(
    tool_name: str,
    tools_requiring_approval: Iterable[str] | None,
) -> bool:
    """Return whether the tool call requires human approval.

    ``None`` means all tools require approval once a handler is configured.
    """
    if tools_requiring_approval is None:
        return True
    return tool_name in tools_requiring_approval
