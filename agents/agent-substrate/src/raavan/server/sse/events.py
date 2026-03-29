"""Typed event system for the agent framework.

``EventBus`` is a thin, typed wrapper around ``asyncio.Queue`` that makes the
event contract between the agent and the SSE layer explicit and discoverable.

Previously, event dicts were created inline throughout ``chat.py``,
``WebHITLBridge``, and ``TaskManagerTool`` with no shared contract.

Usage::

    from raavan.server.sse.events import EventBus, TextDeltaEvent, CompletionEvent

    bus = EventBus()

    # Producer (agent loop)
    await bus.emit(TextDeltaEvent(content="Hello ", partial=True))
    await bus.emit(CompletionEvent(message="Done"))
    bus.close()  # signals the consumer to stop

    # Consumer (SSE route)
    async for event in bus:
        yield f"data: {event.to_sse()}\\n\\n"
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Literal, Optional, Union

logger = logging.getLogger("raavan.server.sse.events")

# Sentinel that marks the end of the event stream
_BUS_DONE = object()

# Public alias — consumers can import BUS_CLOSED to detect stream termination
# without depending on the internal _BUS_DONE name.
BUS_CLOSED = _BUS_DONE


# ---------------------------------------------------------------------------
# Typed event definitions
# ---------------------------------------------------------------------------


@dataclass
class TextDeltaEvent:
    """Streaming text chunk from the LLM."""

    type: Literal["text_delta"] = field(default="text_delta", init=False)
    content: str = ""
    partial: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, "content": self.content, "partial": self.partial}


@dataclass
class ReasoningDeltaEvent:
    """Streaming reasoning/thinking chunk."""

    type: Literal["reasoning_delta"] = field(default="reasoning_delta", init=False)
    content: str = ""
    partial: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, "content": self.content, "partial": self.partial}


@dataclass
class ToolCallEvent:
    """Agent requested a tool execution."""

    type: Literal["tool_call"] = field(default="tool_call", init=False)
    tool_name: str = ""
    input: Dict[str, Any] = field(default_factory=dict)
    call_id: Optional[str] = None
    risk: Optional[str] = None
    color: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "type": self.type,
            "tool_name": self.tool_name,
            "input": self.input,
        }
        if self.call_id:
            d["call_id"] = self.call_id
        if self.risk:
            d["risk"] = self.risk
        if self.color:
            d["color"] = self.color
        return d


@dataclass
class ToolResultEvent:
    """Tool execution completed."""

    type: Literal["tool_result"] = field(default="tool_result", init=False)
    tool_name: str = ""
    result: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "type": self.type,
            "tool_name": self.tool_name,
            "result": self.result,
            "metadata": self.metadata,
        }
        if self.error:
            d["error"] = self.error
        return d


@dataclass
class ToolApprovalRequestEvent:
    """Agent is waiting for human approval of a tool call."""

    type: Literal["tool_approval_request"] = field(
        default="tool_approval_request", init=False
    )
    request_id: str = ""
    tool_name: str = ""
    input: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "requestId": self.request_id,
            "tool_name": self.tool_name,
            "input": self.input,
        }


@dataclass
class HumanInputRequestEvent:
    """Agent is waiting for human input."""

    type: Literal["human_input_request"] = field(
        default="human_input_request", init=False
    )
    request_id: str = ""
    prompt: str = ""
    options: Optional[List[str]] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "type": self.type,
            "requestId": self.request_id,
            "prompt": self.prompt,
        }
        if self.options:
            d["options"] = self.options
        return d


@dataclass
class CompletionEvent:
    """Agent run completed successfully."""

    type: Literal["completion"] = field(default="completion", init=False)
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, "message": self.message}


@dataclass
class ErrorEvent:
    """An error occurred during agent execution."""

    type: Literal["error"] = field(default="error", init=False)
    message: str = ""
    code: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"type": self.type, "message": self.message}
        if self.code:
            d["code"] = self.code
        return d


@dataclass
class RawDictEvent:
    """Escape hatch for arbitrary dict events (task_updated, etc.)."""

    type: str = "raw"
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return self.data


# Union of all known event types
AgentEvent = Union[
    TextDeltaEvent,
    ReasoningDeltaEvent,
    ToolCallEvent,
    ToolResultEvent,
    ToolApprovalRequestEvent,
    HumanInputRequestEvent,
    CompletionEvent,
    ErrorEvent,
    RawDictEvent,
]


# ---------------------------------------------------------------------------
# EventBus
# ---------------------------------------------------------------------------


class EventBus:
    """Typed async event queue bridging the agent loop and the SSE transport.

    Producer side (agent)::

        bus = EventBus()
        await bus.emit(TextDeltaEvent(content="Hello"))
        bus.close()

    Consumer side (SSE route / test)::

        async for event in bus:
            yield f"data: {json.dumps(event.to_dict())}\\n\\n"

    ``close()`` is idempotent — calling it multiple times is safe.
    """

    def __init__(self, maxsize: int = 0) -> None:
        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=maxsize)
        self._closed = False

    # -- Producer interface ---------------------------------------------------

    async def emit(self, event: AgentEvent) -> None:
        """Put *event* on the queue for consumption by the SSE layer."""
        if self._closed:
            logger.debug("EventBus.emit() called on closed bus — dropping %r", event)
            return
        await self._queue.put(event)

    def emit_nowait(self, event: AgentEvent) -> None:
        """Non-blocking emit — raises ``asyncio.QueueFull`` if queue is full."""
        if not self._closed:
            self._queue.put_nowait(event)

    async def emit_dict(self, data: Dict[str, Any]) -> None:
        """Emit a raw dict as a ``RawDictEvent`` (compatibility helper)."""
        await self.emit(RawDictEvent(type=data.get("type", "raw"), data=data))

    def close(self) -> None:
        """Signal no more events will be emitted."""
        if not self._closed:
            self._closed = True
            self._queue.put_nowait(_BUS_DONE)

    # -- Consumer interface ---------------------------------------------------

    def __aiter__(self) -> AsyncIterator[AgentEvent]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[AgentEvent]:
        while True:
            item = await self._queue.get()
            if item is _BUS_DONE:
                return
            yield item  # type: ignore[misc]

    async def get(self) -> Optional[AgentEvent]:
        """Return next event, or ``None`` if the bus is closed."""
        item = await self._queue.get()
        if item is _BUS_DONE:
            return None
        return item  # type: ignore[return-value]

    async def poll(self, timeout: float) -> Any:
        """Return the next item (including ``BUS_CLOSED`` sentinel) within *timeout* seconds.

        Unlike ``get()``, this preserves the ``BUS_CLOSED`` sentinel so callers
        can distinguish a clean shutdown from a timeout.  Raises
        ``asyncio.TimeoutError`` when no item arrives within the window.

        Replaces the old pattern of accessing ``bus._queue.get()`` directly.
        """
        return await asyncio.wait_for(self._queue.get(), timeout=timeout)

    def to_sse_line(self, event: AgentEvent) -> str:
        """Serialize *event* as a single SSE ``data:`` line."""
        return f"data: {json.dumps(event.to_dict())}\n\n"
