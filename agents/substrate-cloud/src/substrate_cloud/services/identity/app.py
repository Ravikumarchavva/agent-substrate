"""Identity Service: FastAPI application (user profiles only; Keycloak signs users in).

Entry point: uvicorn substrate_cloud.services.identity.app:app --port 8010
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from substrate_cloud.services.base import (
    AUTHENTICATED,
    create_service_app,
    init_service_db,
)
from substrate_cloud.services.identity.routes import router
from substrate_cloud.shared.database.base import ServiceBase

import substrate_cloud.services.identity.models  # noqa: F401 — register ORM models before create_all

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
    logger.info("Identity Service started")
    yield
    await engine.dispose()


app = create_service_app(title="Identity Service", lifespan=lifespan)
app.include_router(router, dependencies=AUTHENTICATED)
