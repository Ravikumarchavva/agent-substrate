"""Conversation Service — FastAPI application.

Entry point: uvicorn substrate_cloud.services.conversation.app:app --port 8012
"""

from __future__ import annotations

import logging

import os
from contextlib import asynccontextmanager

from substrate.integrations.cache.redis import RedisConnector
from substrate_cloud.services.base import create_service_app, init_service_db
from substrate_cloud.services.conversation.models import ServiceBase
from substrate_cloud.services.conversation.routes import memory_router, thread_router
from substrate_cloud.shared.events.factory import get_event_bus

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
    redis_connector = RedisConnector(redis_url)
    await redis_connector.connect()
    app.state.redis = redis_connector.client

    event_bus = get_event_bus(redis_url)
    await event_bus.connect()
    app.state.event_bus = event_bus

    logger.info("Conversation service started")
    yield

    # Shutdown
    await event_bus.disconnect()
    await redis_connector.disconnect()
    await engine.dispose()


app = create_service_app(
    title="Conversation Service",
    lifespan=lifespan,
)
app.include_router(thread_router)
app.include_router(memory_router)
