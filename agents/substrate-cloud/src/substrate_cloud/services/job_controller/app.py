"""Job Controller: FastAPI application.

Entry point: uvicorn substrate_cloud.services.job_controller.app:app --port 8013
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from substrate_cloud.services.base import (
    AUTHENTICATED,
    create_service_app,
    init_service_db,
)
from substrate_cloud.services.job_controller.models import ServiceBase
from substrate_cloud.services.job_controller.routes import router
from substrate_cloud.services.job_controller.service import consume_outcomes
from substrate_cloud.shared.events.factory import get_event_bus
from substrate_cloud.shared.service_client import ServiceClient
from substrate_cloud.shared.service_urls import service_url
from substrate_cloud.shared.settings import settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    engine, session_factory = await init_service_db(settings.DATABASE_URL, ServiceBase)
    app.state.engine = engine
    app.state.session_factory = session_factory

    event_bus = get_event_bus(settings.REDIS_URL)
    await event_bus.connect()
    app.state.event_bus = event_bus

    app.state.conversation_client = ServiceClient(
        service_url("conversation"), "conversation"
    )
    app.state.runtime_client = ServiceClient(
        service_url("agent_runtime"), "agent runtime"
    )
    outcomes = asyncio.create_task(consume_outcomes(app), name="job-outcomes")

    logger.info("Job Controller started")
    yield

    outcomes.cancel()
    await asyncio.gather(outcomes, return_exceptions=True)
    await app.state.conversation_client.aclose()
    await app.state.runtime_client.aclose()
    await event_bus.disconnect()
    await engine.dispose()


app = create_service_app(title="Job Controller", lifespan=lifespan)
app.include_router(router, prefix="/api/v1", dependencies=AUTHENTICATED)
