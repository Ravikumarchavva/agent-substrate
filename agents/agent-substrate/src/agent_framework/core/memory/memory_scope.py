"""MemoryScope — controls how agent memory is shared in multi-agent flows."""
from __future__ import annotations

from enum import Enum


class MemoryScope(str, Enum):
    """Controls how an agent's memory participates in a multi-agent flow.

    Use on any ``BaseAgent`` subclass via the ``memory_scope`` constructor arg::

        agent = ReActAgent(
            name="planner",
            ...,
            memory_scope=MemoryScope.SHARED,
        )

    Values
    ------
    ISOLATED
        Default.  The agent owns its own private ``BaseMemory`` instance.
        Messages are never automatically shared with other agents.
        Suitable for stand-alone agents and most sub-agents where information
        leakage between agents is undesirable.

    SHARED
        The agent participates in a flow-level shared memory.  When an
        orchestrator or flow runner creates/binds agents with this scope they
        all write to and read from the *same* ``BaseMemory`` instance.
        Use this when agents need full visibility of the whole conversation
        (e.g. a critic agent reviewing a writer agent's output).

        ⚠ Warning: the system prompt is seeded once; subsequent agents that
        reuse the shared memory should pass ``seed_system_message=False`` to
        avoid duplicate system messages.

    READ_ONLY_SHARED
        The agent can *read* the flow-level shared memory context (injected at
        build time via ``ModelContext``) but all new messages it generates are
        written exclusively to its own private memory.  Useful for specialist
        agents that need full conversational context but should not pollute the
        shared history (e.g. a fact-checker that reads the full thread but only
        returns a verdict).
    """

    ISOLATED = "isolated"
    SHARED = "shared"
    READ_ONLY_SHARED = "read_only_shared"
