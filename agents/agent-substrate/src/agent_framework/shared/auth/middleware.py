"""FastAPI auth middleware and dependencies shared by all services."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from agent_framework.shared.auth.claims import AuthClaims
from agent_framework.shared.auth import jwt as jwt_utils

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)


def _get_jwt_secret(request: Request) -> str:
    """Read JWT_SECRET from the app's service settings."""
    return getattr(request.app.state, "jwt_secret", jwt_utils._DEFAULT_SECRET)


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> AuthClaims:
    """Extract and validate the Bearer JWT. Raises HTTP 401 on failure."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    secret = _get_jwt_secret(request)
    claims = jwt_utils.verify_token(
        credentials.credentials,
        secret=secret,
        expected_type="access",
    )
    if claims is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return claims


def optional_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Optional[AuthClaims]:
    """Same as get_current_user but returns None for anonymous requests."""
    if credentials is None:
        return None
    secret = _get_jwt_secret(request)
    return jwt_utils.verify_token(credentials.credentials, secret=secret)


def require_role(*allowed_roles: str):
    """FastAPI dependency that restricts access to specific platform roles."""

    def checker(user: AuthClaims = Depends(get_current_user)) -> AuthClaims:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user.role}' is not authorized for this action",
            )
        return user

    return checker


def require_service_identity(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> AuthClaims:
    """Require a valid service-to-service runtime token."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing service authorization",
        )
    secret = _get_jwt_secret(request)
    claims = jwt_utils.verify_token(
        credentials.credentials,
        secret=secret,
        expected_type="service",
    )
    if claims is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid service token",
        )
    return claims
