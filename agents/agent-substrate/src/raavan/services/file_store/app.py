"""File Store Service — FastAPI application.

Entry point: uvicorn raavan.services.file_store.app:app --port 8018
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import redis.asyncio as aioredis

from raavan.services.file_store.models import ServiceBase
from raavan.services.file_store.routes import router
from raavan.services.base import create_service_app, init_service_db
from raavan.shared.events.bus import EventBus

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb",
    )
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    # Database
    engine, session_factory = await init_service_db(db_url, ServiceBase)
    app.state.engine = engine
    app.state.session_factory = session_factory

    # Redis + EventBus
    app.state.redis = aioredis.from_url(redis_url, decode_responses=True)

    event_bus = EventBus(redis_url)
    await event_bus.connect()
    app.state.event_bus = event_bus

    # File store — use local filesystem by default
    from raavan.core.storage.local import LocalFileStore

    storage_path = os.environ.get("FILE_STORAGE_PATH", "./data/files")
    app.state.file_store = LocalFileStore(root=storage_path)
    await app.state.file_store.startup()

    logger.info("File Store service started")
    yield

    await app.state.file_store.shutdown()
    await app.state.event_bus.disconnect()
    await app.state.redis.aclose()
    await engine.dispose()


app = create_service_app(
    title="File Store Service",
    lifespan=lifespan,
)
app.include_router(router)
