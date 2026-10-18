"""The authenticated caller, as the control plane sees it."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class Principal:
    subject: str
    name: str
    kind: Literal["user", "service"]
    groups: frozenset[str] = field(default_factory=frozenset)
    email: str | None = None
    # Set when another principal acts for this one (an agent for a user, RFC 8693 `act`).
    actor: str | None = None
    is_admin: bool = False
    # A read-only token may run statements and browse, never change anything.
    read_only: bool = False
    # Where the request came from (agent run, conversation, tenant); recorded with every statement.
    context: dict[str, str] = field(default_factory=dict, compare=False)

    @property
    def id(self) -> str:
        """The principal string used in grants and ownership, e.g. `user:alice`."""
        return f"{self.kind}:{self.name}"

    @property
    def identities(self) -> frozenset[str]:
        """Every grantee this caller matches: itself, its groups and all account users."""
        return frozenset({self.id, "group:account users", *(f"group:{g}" for g in self.groups)})

    def audit(self) -> dict[str, str | None]:
        return {"principal": self.id, "subject": self.subject, "actor": self.actor}
