"""Conversation Service — FastAPI application.

Entry point: uvicorn ravi.services.conversation.app:app --port 8012
"""

from __future__ import annotations
from ravi.logger import setup_logging

import os
from contextlib import asynccontextmanager

import redis.asyncio as aioredis

from ravi.services.base import create_service_app, init_service_db
from ravi.services.conversation.models import ServiceBase
from ravi.services.conversation.routes import memory_router, thread_router
from ravi.shared.events.bus import EventBus

logger = setup_logging()


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

    logger.info("Conversation service started")
    yield

    # Shutdown
    await event_bus.disconnect()
    await app.state.redis.aclose()
    await engine.dispose()


app = create_service_app(
    title="Conversation Service",
    lifespan=lifespan,
)
app.include_router(thread_router)
app.include_router(memory_router)
