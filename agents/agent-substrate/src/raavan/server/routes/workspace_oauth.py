"""Google Workspace OAuth token bridge.

Accepts workspace tokens pushed from the Next.js frontend and makes them
available to the GoogleWorkspaceTool MCP App.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/workspace", tags=["workspace-oauth"])
DEFAULT_SESSION_ID = "default_user"

# In-memory workspace token store (keyed by session_id)
# TODO: Back with Redis for multi-process deployments
_workspace_tokens: Dict[str, Dict[str, Any]] = {}


def store_workspace_tokens(
    *,
    access_token: str,
    refresh_token: str | None = None,
    expires_in: int = 3600,
    session_id: str = DEFAULT_SESSION_ID,
) -> None:
    """Persist mirrored workspace tokens for the current local session."""
    _workspace_tokens[session_id] = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_in": expires_in,
    }


def get_workspace_tokens(session_id: str = DEFAULT_SESSION_ID) -> Dict[str, Any] | None:
    """Return mirrored workspace tokens for the given session."""
    return _workspace_tokens.get(session_id)


def get_workspace_access_token(session_id: str = DEFAULT_SESSION_ID) -> str | None:
    """Return the mirrored workspace access token for the given session."""
    tokens = get_workspace_tokens(session_id)
    if not tokens:
        return None
    token = tokens.get("access_token")
    return token if isinstance(token, str) and token else None


def clear_workspace_tokens(session_id: str = DEFAULT_SESSION_ID) -> None:
    """Remove mirrored workspace tokens for the given session."""
    _workspace_tokens.pop(session_id, None)


@router.post("/set-token")
async def set_workspace_token(request: Request) -> JSONResponse:
    """Accept a Google Workspace OAuth token pushed from the Next.js frontend.

    Called after the user completes the workspace OAuth flow so the backend
    can use the token for Drive, Calendar, and Gmail API calls.
    """
    body = await request.json()
    store_workspace_tokens(
        access_token=body["access_token"],
        refresh_token=body.get("refresh_token"),
        expires_in=body.get("expires_in", 3600),
    )
    logger.info("Google Workspace OAuth token stored from frontend push")
    return JSONResponse({"status": "ok"})


@router.get("/token")
async def get_workspace_token(request: Request) -> JSONResponse:
    """Return the stored Google Workspace access token.

    Used by GoogleWorkspaceTool and the google_workspace.html MCP App.
    """
    tokens = get_workspace_tokens()
    if not tokens:
        raise HTTPException(
            status_code=401,
            detail="Google Workspace not connected. Please connect in Settings > Apps.",
        )
    return JSONResponse({"access_token": tokens["access_token"]})


@router.delete("/token")
async def clear_workspace_token() -> JSONResponse:
    """Clear the stored workspace token (disconnect)."""
    clear_workspace_tokens()
    logger.info("Google Workspace token cleared")
    return JSONResponse({"status": "ok"})
