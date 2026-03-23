"""Identity Auth Service — FastAPI application.

Entry point: uvicorn agent_framework.services.identity.app:app --port 8010
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import redis.asyncio as aioredis

from agent_framework.services.base import create_service_app, init_service_db
from agent_framework.services.identity.routes import router
from agent_framework.shared.database.base import ServiceBase
from agent_framework.shared.events.bus import EventBus

import agent_framework.services.identity.models  # noqa: F401 — register ORM models before create_all

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    # Database
    database_url = os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb",
    )
    engine, session_factory = await init_service_db(database_url, ServiceBase)
    app.state.engine = engine
    app.state.session_factory = session_factory

    # Redis
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    app.state.redis_client = aioredis.from_url(redis_url, decode_responses=True)

    # Event bus
    event_bus = EventBus(redis_url)
    await event_bus.connect()
    app.state.event_bus = event_bus

    # JWT config
    app.state.jwt_secret = os.environ.get(
        "JWT_SECRET",
        "CHANGE_ME_IN_PRODUCTION_USE_A_STRONG_RANDOM_SECRET",
    )

    logger.info("Identity Auth Service started")
    yield

    # Shutdown
    await event_bus.disconnect()
    await app.state.redis_client.aclose()
    await engine.dispose()


app = create_service_app(title="Identity Auth Service", lifespan=lifespan)
app.include_router(router)
