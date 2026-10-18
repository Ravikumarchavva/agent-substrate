"""Conversation Service: FastAPI application.

Entry point: uvicorn substrate_cloud.services.conversation.app:app --port 8012
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from substrate_cloud.services.base import (
    AUTHENTICATED,
    create_service_app,
    init_service_db,
)
from substrate_cloud.services.conversation.models import ServiceBase
from substrate_cloud.services.conversation.routes import router, thread_router
from substrate_cloud.shared.events.factory import get_event_bus
from substrate_cloud.shared.runtime_view import open_runtime_view
from substrate_cloud.shared.settings import settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    engine, session_factory = await init_service_db(settings.DATABASE_URL, ServiceBase)
    app.state.engine = engine
    app.state.session_factory = session_factory

    app.state.store, app.state.runtime = await open_runtime_view()

    event_bus = get_event_bus(settings.REDIS_URL)
    await event_bus.connect()
    app.state.event_bus = event_bus

    logger.info("Conversation service started")
    yield

    await event_bus.disconnect()
    await app.state.store.aclose()
    await engine.dispose()


app = create_service_app(title="Conversation Service", lifespan=lifespan)
app.include_router(thread_router, prefix="/api/v1", dependencies=AUTHENTICATED)
app.include_router(router, prefix="/api/v1", dependencies=AUTHENTICATED)
