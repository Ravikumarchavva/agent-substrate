"""Policy Authorization Service — FastAPI application.

Entry point: uvicorn agent_framework.services.policy.app:app --port 8011
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import redis.asyncio as aioredis

from agent_framework.services.base import create_service_app, init_service_db
from agent_framework.services.policy.routes import router
from agent_framework.shared.database.base import ServiceBase

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    database_url = os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb",
    )
    engine, session_factory = await init_service_db(database_url, ServiceBase)
    app.state.engine = engine
    app.state.session_factory = session_factory

    # Redis cache for policy decisions
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    app.state.redis_client = aioredis.from_url(redis_url, decode_responses=True)

    app.state.jwt_secret = os.environ.get(
        "JWT_SECRET",
        "CHANGE_ME_IN_PRODUCTION_USE_A_STRONG_RANDOM_SECRET",
    )

    # Seed default policies on startup
    async with session_factory() as db:
        from agent_framework.services.policy.service import seed_default_policies
        seeded = await seed_default_policies(db)
        await db.commit()
        if seeded:
            logger.info("Seeded %d default policy rules", seeded)

    logger.info("Policy Authorization Service started")
    yield

    await app.state.redis_client.aclose()
    await engine.dispose()


app = create_service_app(title="Policy Authorization Service", lifespan=lifespan)
app.include_router(router)
