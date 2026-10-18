"""Gateway BFF: FastAPI application.

Entry point: uvicorn substrate_cloud.services.gateway.app:app --port 8001

The only service with a public port. It serves the monolith's Agent API (``/api/v1``, ``contracts/agents/v1``) by composing the
internal services; see ``routes.py``.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter

from substrate_cloud.factory import init_file_access, open_store
from substrate_cloud.services.base import AUTHENTICATED, create_service_app
from substrate_cloud.services.gateway.files import router as files_router
from substrate_cloud.services.gateway.routes import router
from substrate_cloud.shared.service_client import ServiceClient
from substrate_cloud.shared.service_urls import service_url
from substrate_cloud.shared.settings import settings

logger = logging.getLogger(__name__)

UPSTREAMS = ("conversation", "policy", "job_controller", "human_gate", "live_stream")


@asynccontextmanager
async def lifespan(app):
    for name in UPSTREAMS:
        setattr(app.state, f"{name}_client", ServiceClient(service_url(name), name))
    store = open_store(settings)
    app.state.file_access, _ = init_file_access(settings, store)
    logger.info("Gateway BFF started, routing to %d services", len(UPSTREAMS))
    yield
    for name in UPSTREAMS:
        await getattr(app.state, f"{name}_client").aclose()
    await app.state.file_access.aclose()
    await store.aclose()


app = create_service_app(
    title="Gateway BFF",
    lifespan=lifespan,
    cors_origins=settings.CORS_ALLOWED_ORIGINS,
)

api = APIRouter(prefix="/api/v1")
api.include_router(router)
api.include_router(files_router)
app.include_router(api, dependencies=AUTHENTICATED)
