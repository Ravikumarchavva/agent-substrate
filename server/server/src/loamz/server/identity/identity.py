"""The framework-neutral result of verifying a token."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class Identity:
    subject: str
    name: str
    kind: Literal["user", "service"]
    email: str | None = None
    # Immutable Keycloak organization id (or its alias when the token carries no id). None only when the
    # verifier does not require an organization, or for a trusted issuer that sent a `tenant_id` claim instead.
    tenant_id: str | None = None
    organization: str | None = None  # the organization alias, when the token names one
    roles: frozenset[str] = field(default_factory=frozenset)
    groups: frozenset[str] = field(default_factory=frozenset)
    # None: an ordinary user token (limited by roles and grants alone). A set: a delegated token that may use
    # only what these scopes allow (data:read, files:write, ...).
    scopes: frozenset[str] | None = None
    # Every scope in the token (including openid, profile, ...), for callers that need them.
    token_scopes: frozenset[str] = field(default_factory=frozenset)
    actor: str | None = None  # RFC 8693 `act.sub`: who acts for this subject
    token_id: str | None = None  # `jti`
    issuer: str = ""
    trusted_issuer: bool = False  # came from a trusted issuer, never the primary realm
    # Where the request came from (tenant_id, thread_id, run_id, agent_id, token_id), for audit records.
    context: dict[str, str] = field(default_factory=dict, compare=False)

    def has_role(self, *roles: str) -> bool:
        return bool(self.roles & set(roles))

    def allows_scope(self, *needed: str) -> bool:
        """True for an ordinary user token, or a delegated token holding at least one of `needed`."""
        return self.scopes is None or bool(self.scopes & set(needed))
