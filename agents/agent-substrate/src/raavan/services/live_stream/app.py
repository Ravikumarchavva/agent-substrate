"""Live Stream Service — FastAPI application.

Entry point: uvicorn raavan.services.live_stream.app:app --port 8017
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

import redis.asyncio as aioredis

from raavan.services.base import create_service_app
from raavan.services.live_stream.projector import StreamProjector
from raavan.services.live_stream.routes import router
from raavan.shared.events.bus import EventBus

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    app.state.redis = aioredis.from_url(redis_url, decode_responses=True)

    event_bus = EventBus(redis_url)
    await event_bus.connect()
    app.state.event_bus = event_bus

    # Start the stream projector
    projector = StreamProjector(app.state.redis, event_bus)
    app.state.projector = projector

    # Start background event listener
    listener_task = asyncio.create_task(projector.run_event_listener())
    app.state.listener_task = listener_task

    logger.info("Live Stream service started")
    yield

    # Shutdown
    listener_task.cancel()
    try:
        await listener_task
    except asyncio.CancelledError:
        pass

    await event_bus.disconnect()
    await app.state.redis.aclose()


app = create_service_app(
    title="Live Stream Service",
    lifespan=lifespan,
)
app.include_router(router)
