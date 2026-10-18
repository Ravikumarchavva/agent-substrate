"""Download my data."""
from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from loamz.agents.bootstrap.http import CallerDep, use
from loamz.agents.features.people.export.service import ExportService, export

Service = Annotated[ExportService, Depends(use(export))]

router = APIRouter(prefix="/api/v1/me/export", tags=["export"])


@router.get("")
async def export_my_data(caller: CallerDep, service: Service) -> Response:
    document = await service.export(caller)
    return Response(
        content=json.dumps(document, indent=2, default=str),
        media_type="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="my-data.json"',
            "Cache-Control": "no-store",
        },
    )
