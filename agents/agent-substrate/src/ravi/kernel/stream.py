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
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ravi.kernel.content import ContentBlock
from ravi.kernel.identity import AgentId
from ravi.kernel.usage import Usage


# ---------------------------------------------------------------------------
# Token stream events  (LLM output, token by token)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TextDelta:
    """Incremental text content — emitted token-by-token."""

    text: str


@dataclass(frozen=True, slots=True)
class ReasoningDelta:
    """Incremental reasoning / thinking trace — emitted as the model thinks."""

    text: str


@dataclass(frozen=True, slots=True)
class CompletionEvent:
    """Final token-stream event — carries the fully assembled response.

    ``content`` is ``list[ContentBlock]`` so the stream layer stays
    independent of LLM wire formats. ``usage`` carries the token counts
    for this generation; defaults to ``Usage()`` when the adapter does
    not yet populate it.
    """

    content: list[ContentBlock]
    usage: Usage = field(default_factory=Usage)
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StreamDone:
    """End-of-token-stream sentinel. Consumers stop on receipt."""

    reason: str = "complete"


# ---------------------------------------------------------------------------
# Progress events  (supervision tree, every agent at every step)
# ---------------------------------------------------------------------------


class AgentStep:
    """Enumeration of progress step names. Use these string constants."""

    STARTED = "started"
    THINKING = "thinking"       # LLM call in flight
    TOOL_CALL = "tool_call"     # about to execute a tool
    TOOL_RESULT = "tool_result" # tool returned
    HANDOFF = "handoff"         # delegating to a child agent
    PAUSED = "paused"           # agent cooperatively paused by priority preemption
    DONE = "done"               # agent finished successfully
    ERROR = "error"             # agent encountered an unrecoverable error


@dataclass(frozen=True, slots=True)
class AgentProgress:
    """Structured progress event emitted by every agent at every step.

    Every agent MUST emit these at the standard ``AgentStep.*`` points so
    parents and the UI have full visibility into the supervision tree.

    Published to ``TopicId("agent.progress", run_id)`` — ONE topic per
    execution run shared by all agents in the tree. The ``agent_id``,
    ``parent_id``, and ``depth`` fields let the UI reconstruct the hierarchy
    from a single subscription.
    """

    agent_id: AgentId
    step: str                                  # one of AgentStep.*
    content: str                               # human-readable description
    run_id: str = ""                           # routes event to the correct SSE stream
    parent_id: AgentId | None = None           # direct manager; None = root agent
    depth: int = 0                             # org level (0=root, 1=direct report, …)
    metadata: dict[str, str] = field(default_factory=dict)


__all__ = [
    # Token stream
    "TextDelta",
    "ReasoningDelta",
    "CompletionEvent",
    "StreamDone",
    # Progress stream
    "AgentProgress",
    "AgentStep",
]
