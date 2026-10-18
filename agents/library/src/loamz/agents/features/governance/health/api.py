"""`GET /health/ready`, public. Liveness (`GET /health`) is served by `server/app.py`."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response

from loamz.agents.bootstrap.http import use
from loamz.agents.features.governance.health.service import HealthService, health

Service = Annotated[HealthService, Depends(use(health))]

router = APIRouter(prefix="/health", tags=["infra"])


@router.get("/ready")
async def ready(service: Service, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return await service.ready()
