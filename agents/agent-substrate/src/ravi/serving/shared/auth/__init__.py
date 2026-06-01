"""ravi.serving.shared.auth — shared auth utilities."""

from __future__ import annotations

from ravi.serving.shared.auth.claims import AuthClaims
from ravi.serving.shared.auth.jwt import (
    create_access_token,
    create_agent_context_token,
    create_refresh_token,
    create_service_token,
    verify_token,
)
from ravi.serving.shared.auth.middleware import (
    get_current_user,
    optional_current_user,
    require_role,
    require_service_identity,
)

__all__ = [
    "AuthClaims",
    "create_access_token",
    "create_agent_context_token",
    "create_refresh_token",
    "create_service_token",
    "verify_token",
    "get_current_user",
    "optional_current_user",
    "require_role",
    "require_service_identity",
]
