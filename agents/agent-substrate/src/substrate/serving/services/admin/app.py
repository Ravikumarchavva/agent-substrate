"""Admin Control Plane — FastAPI application.

Entry point: uvicorn substrate.serving.services.admin.app:app --port 8019
"""

from __future__ import annotations
from substrate.logger import setup_logging

import os
from contextlib import asynccontextmanager

from substrate.infrastructure.cache.redis import RedisConnector
from substrate.serving.services.admin.models import ServiceBase
from substrate.serving.services.admin.routes import router
from substrate.serving.services.base import create_service_app, init_service_db
from substrate.serving.shared.events.factory import get_event_bus

logger = setup_logging()


@asynccontextmanager
async def lifespan(app):
    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb",
    )
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    engine, session_factory = await init_service_db(db_url, ServiceBase)
    app.state.engine = engine
    app.state.session_factory = session_factory

    redis_connector = RedisConnector(redis_url)
    await redis_connector.connect()
    app.state.redis = redis_connector.client

    event_bus = get_event_bus(redis_url)
    await event_bus.connect()
    app.state.event_bus = event_bus

    logger.info("Admin Control Plane started")
    yield

    await event_bus.disconnect()
    await redis_connector.disconnect()
    await engine.dispose()


app = create_service_app(
    title="Admin Control Plane",
    lifespan=lifespan,
)
app.include_router(router)
