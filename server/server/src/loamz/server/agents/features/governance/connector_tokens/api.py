"""Connector token cache, pushed by the platform after an OAuth callback."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from loamz.server.agents.bootstrap.http import CallerDep, use
from loamz.server.agents.features.governance.connector_tokens.model import (
    Status,
    TokenDelete,
    TokenPush,
)
from loamz.server.agents.features.governance.connector_tokens.service import (
    ConnectorTokenService,
    connector_tokens,
)

Service = Annotated[ConnectorTokenService, Depends(use(connector_tokens))]

router = APIRouter(prefix="/api/v1/internal/connector", tags=["connector-tokens"])


@router.post("/{connector_type}/token", response_model=Status)
async def push_connector_token(
    connector_type: str, body: TokenPush, caller: CallerDep, service: Service
) -> Status:
    await service.push(caller, connector_type, body)
    return Status()


@router.delete("/{connector_type}/token", response_model=Status)
async def delete_connector_token(
    connector_type: str, body: TokenDelete, caller: CallerDep, service: Service
) -> Status:
    await service.delete(caller, connector_type, body.project_id)
    return Status()
