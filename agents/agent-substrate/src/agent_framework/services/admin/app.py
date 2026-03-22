"""Admin Control Plane — FastAPI application.

Entry point: uvicorn agent_framework.services.admin.app:app --port 8019
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import redis.asyncio as aioredis

from agent_framework.services.admin.models import ServiceBase
from agent_framework.services.admin.routes import router
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

    engine, session_factory = await init_service_db(db_url, ServiceBase)
    app.state.engine = engine
    app.state.session_factory = session_factory

    app.state.redis = aioredis.from_url(redis_url, decode_responses=True)
    app.state.event_bus = EventBus(app.state.redis)

    logger.info("Admin Control Plane started")
    yield

    await app.state.redis.aclose()
    await engine.dispose()


app = create_service_app(
    title="Admin Control Plane",
    lifespan=lifespan,
)
app.include_router(router)
