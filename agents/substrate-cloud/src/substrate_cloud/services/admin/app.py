"""Admin Control Plane: FastAPI application.

Entry point: uvicorn substrate_cloud.services.admin.app:app --port 8019
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends

from substrate_cloud.services.admin.models import ServiceBase
from substrate_cloud.services.admin.routes import router
from substrate_cloud.services.admin.service import consume_audit
from substrate_cloud.services.base import create_service_app, init_service_db
from substrate_cloud.shared.auth.middleware import require_role
from substrate_cloud.shared.events.factory import get_event_bus
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
    audit = asyncio.create_task(consume_audit(app), name="audit-consumer")

    logger.info("Admin Control Plane started")
    yield

    audit.cancel()
    await asyncio.gather(audit, return_exceptions=True)
    await event_bus.disconnect()
    await engine.dispose()


app = create_service_app(title="Admin Control Plane", lifespan=lifespan)
# the control-plane view across tenants: platform administrators only
app.include_router(
    router, prefix="/api/v1", dependencies=[Depends(require_role("platform_admin"))]
)
