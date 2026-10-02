"""Human Gate Service — FastAPI application.

Entry point: uvicorn substrate.serving.services.human_gate.app:app --port 8016
"""

from __future__ import annotations
from substrate.logger import setup_logging

import os
from contextlib import asynccontextmanager

from substrate.integrations.cache.redis import RedisConnector
from substrate.serving.services.base import create_service_app, init_service_db
from substrate.serving.services.human_gate.models import ServiceBase
from substrate.serving.services.human_gate.routes import router
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

    # The same physical Postgres database agent_runtime's durable runtime uses (both
    # read DATABASE_URL) — this is what lets resolve_request() wake a signal-suspended
    # run directly instead of only publishing on Redis.
    from substrate.runtime import Runtime
    from substrate.serving.factory import open_store
    from substrate.serving.shared.settings import settings

    store = open_store(settings)
    await store.start()
    runtime_store = Runtime(store).store  # the journal only — no worker is started here
    await runtime_store.start()
    app.state.runtime_store = runtime_store

    logger.info("Human Gate service started")
    yield

    await event_bus.disconnect()
    await redis_connector.disconnect()
    await store.aclose()
    await engine.dispose()


app = create_service_app(
    title="Human Gate Service",
    lifespan=lifespan,
)
app.include_router(router)
