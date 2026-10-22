"""What the caller has used."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from loamz.server.agents.bootstrap.http import CallerDep, use
from loamz.server.agents.features.people.usage.model import UsageOut
from loamz.server.agents.features.people.usage.service import UsageService, usage

Service = Annotated[UsageService, Depends(use(usage))]

router = APIRouter(prefix="/api/v1/me/usage", tags=["usage"])


@router.get("", response_model=UsageOut)
async def get_usage(
    caller: CallerDep, service: Service, days: Annotated[int, Query(ge=1, le=90)] = 30
) -> UsageOut:
    return await service.get(caller, days)
