"""Human Gate Service — FastAPI application.

Entry point: uvicorn substrate.serving.services.human_gate.app:app --port 8016
"""

from __future__ import annotations
from substrate.logger import setup_logging

import os
from contextlib import asynccontextmanager

import redis.asyncio as aioredis

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

    app.state.redis = aioredis.from_url(redis_url, decode_responses=True)

    event_bus = get_event_bus(redis_url)
    await event_bus.connect()
    app.state.event_bus = event_bus

    # Same physical Postgres database agent_runtime's durable Runtime uses
    # (both read DATABASE_URL, see deployment/docker/docker-compose.
    # microservices.yml's shared x-common-env) — this is what lets
    # resolve_request() wake a signal-suspended run directly, converging
    # onto the Phase-1 SignalBus instead of only Redis pub/sub.
    import asyncpg

    from substrate.infrastructure.runtime.pg_signal_bus import PostgresSignalBus

    signal_pool = await asyncpg.create_pool(db_url.replace("+asyncpg", ""))
    signal_bus = PostgresSignalBus(signal_pool)
    await signal_bus.setup()
    app.state.signal_bus = signal_bus
    app.state.signal_pool = signal_pool

    logger.info("Human Gate service started")
    yield

    await event_bus.disconnect()
    await app.state.redis.aclose()
    await signal_pool.close()
    await engine.dispose()


app = create_service_app(
    title="Human Gate Service",
    lifespan=lifespan,
)
app.include_router(router)
