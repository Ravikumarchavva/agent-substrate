"""The caller's cached Google Workspace token."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from loamz.agents.bootstrap.http import CallerDep, use
from loamz.agents.features.governance.workspace_oauth.model import (
    SetWorkspaceToken,
    WorkspaceToken,
)
from loamz.agents.features.governance.workspace_oauth.service import (
    WorkspaceOAuthService,
    workspace_oauth,
)

Service = Annotated[WorkspaceOAuthService, Depends(use(workspace_oauth))]

router = APIRouter(prefix="/api/v1/auth/workspace", tags=["auth"])


@router.get("/token", response_model=WorkspaceToken)
async def get_workspace_token(caller: CallerDep, service: Service) -> WorkspaceToken:
    return await service.get(caller)


@router.put("/token", status_code=204)
async def set_workspace_token(
    body: SetWorkspaceToken, caller: CallerDep, service: Service
) -> Response:
    await service.set(caller, body)
    return Response(status_code=204)


@router.delete("/token", status_code=204)
async def clear_workspace_token(caller: CallerDep, service: Service) -> Response:
    await service.clear(caller)
    return Response(status_code=204)
