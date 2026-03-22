"""File Store Service — FastAPI application.

Entry point: uvicorn agent_framework.services.file_store.app:app --port 8018
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import redis.asyncio as aioredis

from agent_framework.services.file_store.models import ServiceBase
from agent_framework.services.file_store.routes import router
from agent_framework.services.base import create_service_app, init_service_db
from agent_framework.shared.events.bus import EventBus

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
    app.state.event_bus = EventBus(app.state.redis)

    # File store — use local filesystem by default
    from agent_framework.core.storage.local import LocalFileStore

    storage_path = os.environ.get("FILE_STORAGE_PATH", "./data/files")
    app.state.file_store = LocalFileStore(base_path=storage_path)

    logger.info("File Store service started")
    yield

    await app.state.redis.aclose()
    await engine.dispose()


app = create_service_app(
    title="File Store Service",
    lifespan=lifespan,
)
app.include_router(router)
