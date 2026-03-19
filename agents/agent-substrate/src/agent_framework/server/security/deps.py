"""FastAPI authentication dependencies.

Usage
-----
Protected routes (require a valid access token):

    from agent_framework.server.security.deps import get_current_user
    from agent_framework.server.security.jwt import TokenPayload

    @router.get("/chat")
    async def my_route(user: TokenPayload = Depends(get_current_user)):
        ...

Routes that work for both authenticated and anonymous requests:

    @router.get("/public")
    async def public_route(user: TokenPayload | None = Depends(optional_current_user)):
        ...
"""
from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from agent_framework.server.security.jwt import TokenPayload, verify_token

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> TokenPayload:
    """Extract and validate the Bearer JWT from the Authorization header.

    Raises HTTP 401 if the token is missing, expired, or invalid.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = verify_token(credentials.credentials, expected_type="access")
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return payload


def optional_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> TokenPayload | None:
    """Like get_current_user but returns None instead of raising 401.

    Use for routes that serve both authenticated and anonymous users.
    """
    if credentials is None:
        return None
    return verify_token(credentials.credentials, expected_type="access")
