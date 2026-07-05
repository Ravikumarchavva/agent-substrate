"""Live Stream Service — FastAPI application.

Entry point: uvicorn substrate.serving.services.live_stream.app:app --port 8017
"""

from __future__ import annotations
from substrate.logger import setup_logging

import asyncio
import os
from contextlib import asynccontextmanager

from substrate.infrastructure.cache.redis import RedisConnector
from substrate.serving.services.base import create_service_app
from substrate.serving.services.live_stream.projector import StreamProjector
from substrate.serving.services.live_stream.routes import router
from substrate.serving.shared.events.factory import get_event_bus

logger = setup_logging()


@asynccontextmanager
async def lifespan(app):
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    redis_connector = RedisConnector(redis_url)
    await redis_connector.connect()
    app.state.redis = redis_connector.client

    event_bus = get_event_bus(redis_url)
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
    await redis_connector.disconnect()


app = create_service_app(
    title="Live Stream Service",
    lifespan=lifespan,
)
app.include_router(router)
