"""User-facing visibility stream — progress events emitted by agents.

Two independent event channels:

1. **Token stream** (``TextDelta``, ``ReasoningDelta``, ``CompletionEvent``,
   ``StreamDone``) — LLM token-by-token output from the agent currently
   speaking to the user.

2. **Progress stream** (``AgentProgress``) — structured step events emitted
   by every agent in the supervision tree throughout execution. All agents in
   one run publish to ``TopicId("agent.progress", run_id)`` — a single topic
   shared across the whole tree. The UI subscribes once to that topic and
   reconstructs the hierarchy from ``agent_id``, ``parent_id``, and ``depth``.

Standard topic convention (enforced by the agents layer, not the kernel):

    token stream  → TopicId("agent.stream",   agent_id.key)
    progress      → TopicId("agent.progress", run_id)        ← ONE per run

These are pure data types. Transport (SSE, WebSocket, console) lives in the
serving layer.

Sequencing:
    Every event carries a ``seq`` counter that is strictly increasing within
    one run. Over pub/sub transports that may reorder delivery, consumers use
    ``seq`` to reassemble events in emission order.  ``agent_id`` and ``run_id``
    on every event allow demultiplexing concurrent multi-agent streams from a
    single subscription.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field

from ravi.kernel.core.content import ContentBlock
from ravi.kernel.core.identity import AgentId
from ravi.kernel.core.usage import Usage


# ---------------------------------------------------------------------------
# Token stream events  (LLM output, token by token)
# ---------------------------------------------------------------------------


class TextDelta(BaseModel):
    """Incremental text content — emitted token-by-token."""

    text: str
    agent_id: AgentId | None = None
    run_id: str = ""
    seq: int = 0

    model_config = {"frozen": True, "arbitrary_types_allowed": True}


class ReasoningDelta(BaseModel):
    """Incremental reasoning / thinking trace — emitted as the model thinks."""

    text: str
    agent_id: AgentId | None = None
    run_id: str = ""
    seq: int = 0

    model_config = {"frozen": True, "arbitrary_types_allowed": True}


class CompletionEvent(BaseModel):
    """Final token-stream event — carries the fully assembled response."""

    content: list[ContentBlock]
    usage: Usage = Field(default_factory=Usage)
    metadata: dict[str, str] = Field(default_factory=dict)
    agent_id: AgentId | None = None
    run_id: str = ""
    seq: int = 0

    model_config = {"frozen": True, "arbitrary_types_allowed": True}


class StreamDone(BaseModel):
    """End-of-token-stream sentinel. Consumers stop on receipt."""

    reason: str = "complete"

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Progress events  (supervision tree, every agent at every step)
# ---------------------------------------------------------------------------


class AgentStep(StrEnum):
    """Standard agent progress step names.

    Using ``StrEnum`` ensures ``AgentProgress.step`` is always a known
    value and enables exhaustive matching in consumers.
    """

    STARTED = "started"
    THINKING = "thinking"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    HANDOFF = "handoff"
    PAUSED = "paused"
    DONE = "done"
    ERROR = "error"


class AgentProgress(BaseModel):
    """Structured progress event emitted by every agent at every step.

    Published to ``TopicId("agent.progress", run_id)`` — ONE topic per
    execution run shared by all agents in the tree. The ``agent_id``,
    ``parent_id``, and ``depth`` fields let the UI reconstruct the hierarchy
    from a single subscription.

    ``seq`` is strictly increasing within one run so subscribers can detect
    gaps and reorder out-of-order deliveries from pub/sub transports.

    ``ts`` is the emission wall-clock time (UTC). Use for display only;
    use ``seq`` for ordering.
    """

    agent_id: AgentId
    step: AgentStep
    content: str
    run_id: str = ""
    parent_id: AgentId | None = None
    depth: int = 0
    seq: int = 0
    ts: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    metadata: dict[str, str] = Field(default_factory=dict)

    model_config = {"frozen": True, "arbitrary_types_allowed": True}


__all__ = [
    "TextDelta",
    "ReasoningDelta",
    "CompletionEvent",
    "StreamDone",
    "AgentProgress",
    "AgentStep",
]
