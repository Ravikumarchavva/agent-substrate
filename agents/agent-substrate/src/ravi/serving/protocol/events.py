"""Wire events — the single source of truth for the engine→UI SSE protocol.

Every event the engine streams to the UI is one of the Pydantic models below.
They form a discriminated union on the ``type`` field (``WireEvent``). The UI's
TypeScript types are generated from this module's JSON Schema, so the two sides
cannot drift.

Conventions (enforced here, relied on by the UI):
  * ``type`` is a dotted, namespaced string: ``<domain>.<event>``.
  * All fields are snake_case.
  * Every event is JSON-serializable with ``model_dump(mode="json")``.

A note on layering: these are *wire* types, distinct from the kernel stream
types in ``ravi.kernel.stream``. ``serving/stream/mapper.py`` is the only place
that translates kernel → wire.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

from ravi.serving.protocol.version import PROTOCOL_VERSION


# ---------------------------------------------------------------------------
# Handshake
# ---------------------------------------------------------------------------


class HelloEvent(BaseModel):
    """First event on every stream. Carries the protocol version for the client
    to assert against its own generated types."""

    type: Literal["protocol.hello"] = "protocol.hello"
    version: str = PROTOCOL_VERSION


# ---------------------------------------------------------------------------
# Token stream (LLM output)
# ---------------------------------------------------------------------------


class TextDeltaEvent(BaseModel):
    """Incremental assistant text, token by token."""

    type: Literal["text.delta"] = "text.delta"
    text: str


class ReasoningDeltaEvent(BaseModel):
    """Incremental reasoning / thinking trace."""

    type: Literal["reasoning.delta"] = "reasoning.delta"
    text: str


# ---------------------------------------------------------------------------
# Tool + delegation progress (whole supervision tree)
# ---------------------------------------------------------------------------


class ToolCallEvent(BaseModel):
    """An agent is about to execute a tool."""

    type: Literal["tool.call"] = "tool.call"
    call_id: str = ""
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    agent: str = ""           # which agent in the tree ran it (agent_id.key)
    depth: int = 0            # 0 = root agent, 1 = direct subagent, …
    risk: str | None = None   # ToolRisk value, when known


class ToolResultEvent(BaseModel):
    """A tool finished."""

    type: Literal["tool.result"] = "tool.result"
    call_id: str = ""
    tool_name: str
    ok: bool = True
    output: str = ""
    error: str | None = None
    agent: str = ""
    depth: int = 0


class HandoffEvent(BaseModel):
    """An orchestrator delegated to a subagent."""

    type: Literal["agent.handoff"] = "agent.handoff"
    source_agent: str = ""
    target_agent: str = ""
    reason: str = ""
    depth: int = 0


# ---------------------------------------------------------------------------
# Turn / run lifecycle
# ---------------------------------------------------------------------------


class ToolCallSummary(BaseModel):
    """A tool call attached to a completed assistant turn (for history rebuild)."""

    id: str = ""
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    risk: str | None = None


class Attachment(BaseModel):
    """A file produced during the turn (image, document, …)."""

    id: str
    thread_id: str | None = None
    name: str
    mime: str = "application/octet-stream"
    size: int = 0
    url: str | None = None


class TurnCompletedEvent(BaseModel):
    """One assistant turn finished. If ``tool_calls`` is non-empty the agent will
    continue after the tools run (another turn follows); otherwise this is the
    final assistant message of the run."""

    type: Literal["turn.completed"] = "turn.completed"
    text: str = ""
    tool_calls: list[ToolCallSummary] = Field(default_factory=list)
    attachments: list[Attachment] = Field(default_factory=list)
    finish_reason: str = "stop"


class RunCompletedEvent(BaseModel):
    """The whole agent run finished successfully."""

    type: Literal["run.completed"] = "run.completed"


class RunFailedEvent(BaseModel):
    """The run terminated with an unrecoverable error."""

    type: Literal["run.failed"] = "run.failed"
    error: str = ""
    code: str | None = None


class RunCancelledEvent(BaseModel):
    """The run was cancelled by the client."""

    type: Literal["run.cancelled"] = "run.cancelled"


# ---------------------------------------------------------------------------
# Human-in-the-loop
# ---------------------------------------------------------------------------


class ApprovalRequestedEvent(BaseModel):
    """The agent is waiting for the human to approve a tool call."""

    type: Literal["approval.requested"] = "approval.requested"
    request_id: str
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)


class InputRequestedEvent(BaseModel):
    """The agent is waiting for free-form human input."""

    type: Literal["input.requested"] = "input.requested"
    request_id: str
    prompt: str
    options: list[str] | None = None


# ---------------------------------------------------------------------------
# Task board (kanban)
# ---------------------------------------------------------------------------


class TaskCreatedEvent(BaseModel):
    type: Literal["task.created"] = "task.created"
    task_list: dict[str, Any] = Field(default_factory=dict)


class TaskUpdatedEvent(BaseModel):
    type: Literal["task.updated"] = "task.updated"
    task: dict[str, Any] = Field(default_factory=dict)


class TaskAddedEvent(BaseModel):
    type: Literal["task.added"] = "task.added"
    task: dict[str, Any] = Field(default_factory=dict)


class TaskDeletedEvent(BaseModel):
    type: Literal["task.deleted"] = "task.deleted"
    task_id: str


# ---------------------------------------------------------------------------
# Transport-level
# ---------------------------------------------------------------------------


class ErrorEvent(BaseModel):
    """A serving-level error (distinct from ``run.failed`` which is agent-level)."""

    type: Literal["error"] = "error"
    message: str
    code: str | None = None


class PingEvent(BaseModel):
    """Keep-alive heartbeat."""

    type: Literal["ping"] = "ping"


# ---------------------------------------------------------------------------
# Discriminated union
# ---------------------------------------------------------------------------

WireEvent = Annotated[
    Union[
        HelloEvent,
        TextDeltaEvent,
        ReasoningDeltaEvent,
        ToolCallEvent,
        ToolResultEvent,
        HandoffEvent,
        TurnCompletedEvent,
        RunCompletedEvent,
        RunFailedEvent,
        RunCancelledEvent,
        ApprovalRequestedEvent,
        InputRequestedEvent,
        TaskCreatedEvent,
        TaskUpdatedEvent,
        TaskAddedEvent,
        TaskDeletedEvent,
        ErrorEvent,
        PingEvent,
    ],
    Field(discriminator="type"),
]


__all__ = [
    "HelloEvent",
    "TextDeltaEvent",
    "ReasoningDeltaEvent",
    "ToolCallEvent",
    "ToolResultEvent",
    "HandoffEvent",
    "ToolCallSummary",
    "Attachment",
    "TurnCompletedEvent",
    "RunCompletedEvent",
    "RunFailedEvent",
    "RunCancelledEvent",
    "ApprovalRequestedEvent",
    "InputRequestedEvent",
    "TaskCreatedEvent",
    "TaskUpdatedEvent",
    "TaskAddedEvent",
    "TaskDeletedEvent",
    "ErrorEvent",
    "PingEvent",
    "WireEvent",
]
