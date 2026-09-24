"""Window-slicing invariant shared by the strategies that cut history by size."""

from __future__ import annotations

from substrate.kernel.core.content import ChatMessage, Role


def drop_orphaned_tool_results(window: list[ChatMessage]) -> list[ChatMessage]:
    """Cut a window's leading tool results off.

    A window taken from the tail of history can start on a tool result whose
    assistant tool-call fell outside it. Every provider rejects a tool result
    with no matching call, so those results go with their call.
    """
    start = 0
    while start < len(window) and window[start].role == Role.TOOL:
        start += 1
    return window[start:]


__all__ = ["drop_orphaned_tool_results"]
