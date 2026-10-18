"""Who is asking: the verified caller every feature service receives, never built from request bodies."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from loamz.data.storage.errors import ForbiddenError


@dataclass(frozen=True, slots=True)
class Caller:
    """A verified principal, scoped to one tenant (a Keycloak organization).

    `scopes is None` is an ordinary user token, limited by roles and ownership alone; a set is a delegated token that
    may do only what those scopes allow.
    """

    tenant_id: str
    user_id: str
    kind: Literal["user", "service"] = "user"
    roles: frozenset[str] = field(default_factory=frozenset)
    scopes: frozenset[str] | None = None
    email: str | None = None
    name: str | None = None

    def has_role(self, *roles: str) -> bool:
        return bool(self.roles & set(roles))

    def require_role(self, *roles: str) -> None:
        if not self.has_role(*roles):
            raise ForbiddenError(f"needs one of the roles: {', '.join(roles)}")
