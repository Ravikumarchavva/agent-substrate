"""Chat wire-event helpers — image payloads, tool metadata, inline persistence.

Split out of ``chat.py``.
"""

from __future__ import annotations
from substrate.logger import setup_logging

from dataclasses import dataclass
from typing import Any

from substrate.kernel import ChatMessage
from substrate.kernel.core.content import TextBlock, ToolUseBlock
from substrate.serving.monolith.services.agent_service import (
    persist_assistant_message,
    persist_tool_result,
)
from substrate.serving.protocol import TurnCompletedEvent, ToolResultEvent

logger = setup_logging()


@dataclass
class _ImagePayload:
    """Raw image binary for multimodal user messages."""

    data: bytes
    media_type: str


MediaType = str | _ImagePayload


def _build_tool_meta_map(tools: list) -> dict:
    """Build a mapping of tool_name → { risk, color, ui? } for event enrichment."""
    from substrate.kernel.tools import ToolRisk

    meta_map: dict = {}
    for tool in tools:
        name = getattr(tool, "name", None)
        if not name:
            continue
        risk = getattr(tool, "risk", ToolRisk.SAFE)
        color = (
            "red"
            if risk == ToolRisk.CRITICAL
            else "yellow"
            if risk == ToolRisk.HIGH
            else "green"
        )
        entry: dict = {"risk": str(risk), "color": color}
        ui = getattr(tool, "ui", None)
        if ui:
            entry["ui"] = ui
        meta_map[name] = entry
    return meta_map


class _WirePersister:
    """Persists wire events to Postgres inline as the run streams.

    Implements the ``stream.Persister`` protocol. ``persist_turn`` writes the
    assistant message (text + tool calls, enriched with MCP-App UI metadata via
    ``tool_meta_map``); ``persist_tool`` records error tool results so reloads
    can show failures. Each write opens its own DB session so a slow write never
    blocks the stream's own transaction.
    """

    def __init__(
        self,
        *,
        session_factory: Any,
        thread_id: Any,
        tool_meta_map: dict,
        attachments: list | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._thread_id = thread_id
        self._tool_meta_map = tool_meta_map
        self._attachments = attachments or []

    async def persist_turn(self, event: TurnCompletedEvent) -> None:
        content: list[Any] = []
        if event.text:
            content.append(TextBlock(text=event.text))
        for tc in event.tool_calls:
            content.append(
                ToolUseBlock(call_id=tc.id, tool_name=tc.name, arguments=tc.args)
            )
        if not content:
            return
        message = ChatMessage(role="assistant", content=content)
        metadata = {"attachments": self._attachments} if self._attachments else None
        try:
            async with self._session_factory() as db:
                await persist_assistant_message(
                    db,
                    self._thread_id,
                    message,
                    tool_meta_map=self._tool_meta_map,
                    metadata=metadata,
                )
                await db.commit()
        except Exception:
            logger.exception("Failed to persist assistant turn")

    async def persist_tool(self, event: ToolResultEvent) -> None:
        # ask_human results carry the user's answer; persist them (even on
        # success) so the answered HITL card can be rebuilt on reload. All other
        # successful results are reconstructed from the assistant turn.
        is_ask_human = event.tool_name == "ask_human"
        if event.ok and not is_ask_human:
            return
        output = event.output if event.ok else (event.error or "")
        try:
            async with self._session_factory() as db:
                await persist_tool_result(
                    db,
                    self._thread_id,
                    event.call_id,
                    event.tool_name,
                    output,
                    is_error=not event.ok,
                )
                await db.commit()
        except Exception:
            logger.exception("Failed to persist tool result")


__all__ = [
    "_ImagePayload",
    "MediaType",
    "_build_tool_meta_map",
    "_WirePersister",
]
