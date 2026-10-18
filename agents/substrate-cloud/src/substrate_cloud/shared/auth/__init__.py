"""substrate_cloud.shared.auth: Keycloak token verification shared by all services."""

from __future__ import annotations

from substrate_cloud.shared.auth.claims import AuthClaims
from substrate_cloud.shared.auth.middleware import (
    get_current_user,
    optional_current_user,
    require_role,
    require_scopes,
    require_service_identity,
)
from substrate_cloud.shared.auth.verifier import build_verifier, get_verifier

__all__ = [
    "AuthClaims",
    "build_verifier",
    "get_current_user",
    "get_verifier",
    "optional_current_user",
    "require_role",
    "require_scopes",
    "require_service_identity",
]
