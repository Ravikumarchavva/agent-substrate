"""Mutation request / permission contracts for self-evolution safeguards.

An agent that wants to modify itself — rewrite its system prompt, add a tool,
remove a tool, push a weight update, or visibly diverge from baseline behavior
— must first ask a :class:`MutationPolicy` for permission. The policy returns
a :class:`MutationPermission` recording the decision; only on ``granted=True``
may the runtime apply the change.

Why a contract at the kernel layer?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Self-evolution is the most dangerous capability the fabric ever exposes — a
rogue or compromised agent that can rewrite its own prompt, attach arbitrary
tools, or quietly drift from spec is the single highest-risk failure mode of
a multi-tenant agent platform. The kernel formalises the decision boundary
so every runtime, regardless of policy implementation, asks the same
question in the same shape before mutating an agent.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Protocol, runtime_checkable

__all__ = [
    "MutationKind",
    "MutationPermission",
    "MutationPolicy",
    "MutationRequest",
]


class MutationKind(Enum):
    """Class of self-modification an agent might attempt.

    Each variant maps to a distinct policy concern; ``WEIGHT_UPDATE`` in
    particular is forbidden by default because in-place weight mutation is
    indistinguishable from a model swap and so must always be gated through
    the operator-managed model registry rather than self-driven.
    """

    PROMPT_REWRITE = auto()       # Agent wants to alter its own system prompt
    TOOL_ADD = auto()             # Agent wants to attach a new tool to itself
    TOOL_REMOVE = auto()          # Agent wants to detach an existing tool
    WEIGHT_UPDATE = auto()        # Agent wants to push a weight delta
    BEHAVIOR_DIVERGENCE = auto()  # Detected drift from baseline behavior spec


@dataclass(frozen=True, slots=True)
class MutationRequest:
    """A request to mutate an agent.

    ``family_depth`` is the depth of this agent inside its evolutionary
    family tree (the parent agent's depth + 1). The policy compares it
    against a ceiling so chains of agents-spawning-agents cannot escape
    the safeguards via depth.

    ``payload_summary`` is a short human-readable string describing the
    proposed change — never the full diff. The policy decides based on
    *kind* and *origin*, not by inspecting the payload itself.
    """

    request_id: str
    principal_fqn: str
    target_agent_fqn: str
    kind: MutationKind
    family_depth: int
    payload_summary: str
    requested_at: str  # ISO-8601


@dataclass(frozen=True, slots=True)
class MutationPermission:
    """Outcome of a :class:`MutationPolicy` decision.

    ``granted=False`` carries a short machine-readable ``reason`` (e.g.
    ``"forbidden_kind"``, ``"family_depth_ceiling"``, ``"rate_limited"``).
    ``expires_at`` — when non-None — bounds the grant in time so a
    long-lived permission cannot be replayed forever.
    """

    request_id: str
    granted: bool
    reason: str
    decided_at: str  # ISO-8601
    expires_at: str | None = None


@runtime_checkable
class MutationPolicy(Protocol):
    """Gatekeeper contract — decides whether a :class:`MutationRequest` is allowed.

    Implementations may consult any combination of: forbidden-kind lists,
    family-depth ceilings, per-principal rate limits, trust scores, or
    operator-defined policy DSLs. The kernel only requires that a decision
    is returned in bounded time.
    """

    async def evaluate(
        self, request: MutationRequest
    ) -> MutationPermission:
        """Return a :class:`MutationPermission` decision for ``request``."""
        ...
