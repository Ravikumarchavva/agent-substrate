"""Rate-limit status: read-only, counts nothing."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from loamz.agents.bootstrap.http import OptionalCallerDep, rate_key, use
from loamz.agents.features.governance.rate_limit.service import RateLimitService, rate_limit

Service = Annotated[RateLimitService, Depends(use(rate_limit))]

router = APIRouter(prefix="/api/v1/rate-limit", tags=["rate-limit"])


@router.get("/status")
async def rate_limit_status(
    request: Request, caller: OptionalCallerDep, service: Service
) -> dict[str, Any]:
    """The caller's current usage of its request window (an anonymous client is counted by address)."""
    key, tier = rate_key(request, caller)
    return await service.status(key, authenticated=tier == "authenticated")
