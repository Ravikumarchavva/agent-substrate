"""Human Gate Service: FastAPI application.

Entry point: uvicorn substrate_cloud.services.human_gate.app:app --port 8016
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from substrate_cloud.services.base import AUTHENTICATED, create_service_app
from substrate_cloud.services.human_gate.routes import router
from substrate_cloud.shared.runtime_view import open_runtime_view
from substrate_cloud.shared.service_client import ServiceClient
from substrate_cloud.shared.service_urls import service_url

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    app.state.store, app.state.runtime = await open_runtime_view()
    app.state.conversation_client = ServiceClient(
        service_url("conversation"), "conversation"
    )
    logger.info("Human Gate service started")
    yield
    await app.state.conversation_client.aclose()
    await app.state.store.aclose()


app = create_service_app(title="Human Gate Service", lifespan=lifespan)
app.include_router(router, prefix="/api/v1", dependencies=AUTHENTICATED)
