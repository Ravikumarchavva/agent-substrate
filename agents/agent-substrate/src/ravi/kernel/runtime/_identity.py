"""Identity value-objects for the agent runtime.

``AgentId`` and ``TopicId`` are routing keys used throughout the runtime.
``PrincipalId`` is a globally unique, content-addressed identity for any
actor.  ``DelegationToken`` carries proof of delegated authority.
``IdentityContext`` is the Envelope carrier for the acting principal.

All types are pure Python — no external dependencies beyond the standard library.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto


class PrincipalKind(Enum):
    HUMAN = auto()
    AGENT = auto()
    TOOL = auto()
    WORKFLOW = auto()
    SERVICE = auto()
    SYSTEM = auto()


@dataclass(frozen=True, slots=True)
class AgentId:
    """Routing key for a logical agent — stable, durable, content-addressed."""

    type: str
    key: str

    def __str__(self) -> str:
        return f"{self.type}/{self.key}"

    @classmethod
    def generate(cls, agent_type: str) -> AgentId:
        return cls(type=agent_type, key=uuid.uuid4().hex)


@dataclass(frozen=True, slots=True)
class TopicId:
    """Routing key for a pub/sub topic."""

    type: str
    source: str

    def __str__(self) -> str:
        return f"{self.type}/{self.source}"


@dataclass(frozen=True, slots=True)
class PrincipalId:
    """Globally unique, content-addressed identity for any actor in the fabric."""

    kind: PrincipalKind
    tenant_id: str
    workspace_id: str
    # Stable external name (human/agent/tool registered name)
    name: str
    # Immutable UUID assigned at registration time
    uid: str = field(default_factory=lambda: uuid.uuid4().hex)

    @property
    def fqn(self) -> str:
        """Fully-qualified name: kind/tenant/workspace/name"""
        return f"{self.kind.name.lower()}/{self.tenant_id}/{self.workspace_id}/{self.name}"

    @property
    def fingerprint(self) -> str:
        """Deterministic content-addressed fingerprint for deduplication."""
        raw = f"{self.kind.name}:{self.tenant_id}:{self.workspace_id}:{self.name}:{self.uid}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def as_agent_id(self) -> AgentId:
        """Bind this principal to the runtime routing key."""
        return AgentId(type=self.kind.name.lower(), key=self.uid)


@dataclass(frozen=True, slots=True)
class DelegationToken:
    """A proof that ``delegator`` has granted ``delegate`` permission to act on its behalf."""

    delegator: PrincipalId
    delegate: PrincipalId
    scopes: tuple[str, ...]           # e.g. ("tool:execute", "memory:read")
    expires_at: str | None = None     # ISO-8601 wall-clock; None = no expiry
    token_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def is_expired(self, now_iso: str) -> bool:
        if self.expires_at is None:
            return False
        return now_iso > self.expires_at


@dataclass(frozen=True, slots=True)
class IdentityContext:
    """Carrier for the acting principal and optional delegation chain in an Envelope."""

    principal: PrincipalId
    # Delegation chain from outermost authority to current actor (oldest first)
    delegation_chain: tuple[DelegationToken, ...] = field(default_factory=tuple)

    @property
    def effective_tenant_id(self) -> str:
        return self.principal.tenant_id

    @property
    def effective_workspace_id(self) -> str:
        return self.principal.workspace_id

    def with_delegation(self, token: DelegationToken) -> IdentityContext:
        return IdentityContext(
            principal=self.principal,
            delegation_chain=(*self.delegation_chain, token),
        )


__all__ = [
    "AgentId",
    "TopicId",
    "PrincipalId",
    "PrincipalKind",
    "DelegationToken",
    "IdentityContext",
]
