"""Policy Authorization Service — FastAPI application.

Entry point: uvicorn substrate_cloud.services.policy.app:app --port 8011
"""

from __future__ import annotations

import logging

from contextlib import asynccontextmanager

from substrate_cloud.services.base import (
    AUTHENTICATED,
    create_service_app,
    init_service_db,
)
from substrate_cloud.services.policy.routes import router
from substrate_cloud.shared.database.base import ServiceBase
from substrate_cloud.shared.settings import settings

import substrate_cloud.services.policy.models  # noqa: F401 — register ORM models before create_all

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    engine, session_factory = await init_service_db(settings.DATABASE_URL, ServiceBase)
    app.state.engine = engine
    app.state.session_factory = session_factory

    # Seed default policies on startup
    async with session_factory() as db:
        from substrate_cloud.services.policy.service import seed_default_policies

        seeded = await seed_default_policies(db)
        await db.commit()
        if seeded:
            logger.info("Seeded %d default policy rules", seeded)

    logger.info("Policy Authorization Service started")
    yield

    await engine.dispose()


app = create_service_app(title="Policy Authorization Service", lifespan=lifespan)
app.include_router(router, prefix="/api/v1", dependencies=AUTHENTICATED)
