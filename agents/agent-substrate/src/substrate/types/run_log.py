"""RunLogEntry and EventLogProtocol — the append-only durable spine of every run.

Named ``RunLogEntry`` (not ``RunEvent``) to avoid collision with
``integrations/events/envelope.py::EventEnvelope`` (the generic pub/sub
envelope — a different thing).

Truth model
-----------
A run's state is ``fold(entries from seq=0)`` — implemented by
``agents/runtime/effect_cache.py::EffectCache.fold()``, folded once per lease.
There is no separate checkpoint/snapshot type; nothing in this codebase
implements one today. If fold cost ever exceeds budget at P99, a log-
compaction snapshot would be the fix — evaluate then, not preemptively.

Optimistic concurrency
----------------------
``append()`` takes ``expected_seq`` — the caller's view of the current last
sequence number.  If the store's actual last_seq differs, it raises
``ConcurrentAppendError``.  This fences two workers from writing to the same
run simultaneously without a distributed lock.

Kinds
-----
``RunLogKind`` names the **core** kinds the runtime, replay and streaming depend
on. A kind belongs here when more than one module or layer emits or consumes it;
a kind private to one module (``ask.*``, ``join.completed``, ``hitl.question``,
``feed.curated``) stays a free string. Its values are the exact strings persisted in existing logs — never rename
one, or old runs stop replaying. ``RunLogEntry.kind`` stays a plain ``str`` on
purpose: application-level kinds (``ask.*``, ``subagent.*``, ``feed.curated`` …)
are free-form dotted-lowercase strings that do not belong in the kernel, and a
reader must still load a log containing a kind it has never seen.

Versioning
----------
``RunLogEntry.v`` is the schema version of the persisted entry. Bump it only for
a change old readers cannot handle; readers must keep accepting every version
they have ever written.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field

from substrate.types.content import JsonObject
from substrate.types.run_status import RunId


class RunLogKind(StrEnum):
    """Core run-log kinds. Values are persisted — see the module docstring."""

    # Run lifecycle
    RUN_STARTED = "run.started"
    RUN_RESUMED = "run.resumed"
    RUN_SUSPENDED = "run.suspended"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"
    # Completed, but cut short (out of steps): the final answer is best-effort.
    RUN_TRUNCATED = "run.truncated"
    # An attempt failed and the run will be tried again after a backoff.
    RUN_RETRYING = "run.retrying"

    # Journaled effects (replayed from cache, never re-executed)
    # Committed before an effect runs; its outcome is EFFECT_RESULT. An intent with
    # no outcome means the effect is in doubt (the worker died mid-flight).
    EFFECT_INTENT = "effect.intent"
    EFFECT_RESULT = "effect.result"
    LLM_CALL = "llm.call"
    # The assistant's whole reply for one LLM call: text and reasoning. Durable, unlike the
    # token stream (TEXT_DELTA), which is live output that is dropped after the run ends.
    ASSISTANT_MESSAGE = "assistant.message"
    TOOL_CALL = "tool.call"
    TOOL_RESULT = "tool.result"

    # Conversation and human-in-the-loop
    USER_MESSAGE = "user.message"
    USER_MESSAGE_FLAGGED = "user.message.flagged"  # marker referencing a user.message seq
    MCP_APP_CONTEXT = "mcp_app_context"  # legacy underscore spelling — persisted, keep
    INPUT_REQUESTED = "input.requested"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_DECIDED = "approval.decided"  # who decided, when and why; written once per request

    # Supervision
    CHILD_SPAWNED = "child.spawned"
    SUBAGENT_START = "subagent.start"
    SUBAGENT_DONE = "subagent.done"

    # Live streaming (not part of replay state)
    TEXT_DELTA = "text.delta"
    REASONING_DELTA = "reasoning.delta"


class RunLogEntry(BaseModel):
    """A single immutable entry in a run's event log.

    ``seq`` is monotonically increasing within a run, starting at 0.
    ``kind`` is a dot-namespaced string; core kinds are ``RunLogKind`` members.
    ``payload`` is a free-form JSON dict; callers type-narrow on ``kind``.
    ``v`` is the entry schema version (see module docstring).
    """

    run_id: RunId
    seq: int = Field(ge=0)
    kind: str
    v: int = 1
    payload: JsonObject = Field(default_factory=dict)
    ts: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))

    model_config = {"frozen": True}


__all__ = ["RunLogKind", "RunLogEntry"]
