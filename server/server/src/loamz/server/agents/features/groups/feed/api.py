from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from loamz.server.agents.bootstrap.http import CallerDep, use
from loamz.server.agents.features.groups.feed.model import FeedIn
from loamz.server.agents.features.groups.feed.service import FeedService, feed

Service = Annotated[FeedService, Depends(use(feed))]

router = APIRouter(tags=["feed"])


@router.post("/api/v1/feed")
@router.post("/feed", include_in_schema=False)
async def open_feed(body: FeedIn, caller: CallerDep, service: Service) -> StreamingResponse:
    return StreamingResponse(
        service.stream(caller, body),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
