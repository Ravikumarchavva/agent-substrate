"""The caller of a request, as every route of the platform sees it.

Built from a verified Keycloak access token (``substrate_identity.Identity``) by :meth:`AuthClaims.from_identity`; nothing else
creates one in production. ``tenant_id`` is the immutable Keycloak organization id of the one organization active in the token.
"""

from __future__ import annotations

from pydantic import BaseModel
from substrate_identity import Identity

# Platform roles in decreasing order of power; the first one a token holds is its ``role``.
ROLES = ("platform_admin", "tenant_admin", "workspace_admin", "end_user")


class AuthClaims(BaseModel):
    sub: str  # the Keycloak user (or service account) id
    email: str = ""
    role: str = "end_user"
    # Empty only for a platform service account, which acts across tenants and names the tenant in the request.
    tenant_id: str = ""
    kind: str = "user"  # user | service
    # None: an ordinary user token. A set: a delegated token that may use only what these scopes allow.
    scopes: frozenset[str] | None = None
    jti: str = ""

    @classmethod
    def from_identity(cls, who: Identity) -> AuthClaims:
        role = next((r for r in ROLES if who.has_role(r)), "end_user")
        return cls(
            sub=who.subject,
            email=who.email or "",
            role=role,
            tenant_id=who.tenant_id or "",
            kind=who.kind,
            scopes=who.scopes,
            jti=who.token_id or "",
        )

    @property
    def is_admin(self) -> bool:
        return self.role in ("platform_admin", "tenant_admin")

    @property
    def is_service(self) -> bool:
        return self.kind == "service"

    def allows_scope(self, *needed: str) -> bool:
        return self.scopes is None or bool(self.scopes & set(needed))
