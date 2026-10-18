"""Substrate Data control plane: governed catalog, statement planning and workload API.

Every `/api/v1` route requires an OIDC access token. The control plane never reads table data:
it plans, authorizes and signs; attached compute reads and writes object storage directly.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from substrate_data.api.limits import RateLimiter
from substrate_data.api.publications import Publisher
from substrate_data.api.routes import (
    assets,
    catalog,
    files,
    imports,
    maintenance,
    materialized,
    policies,
    statements,
    views,
    workloads,
)
from substrate_data.api.services import ControllerClient, Services
from substrate_data.auth.oidc import TokenVerifier
from substrate_data.auth.tickets import TicketSigner
from substrate_data.catalog.store import CatalogStore
from substrate_data.core import http
from substrate_data.core.errors import Problem
from substrate_data.core.models import Settings
from substrate_data.core.tls import internal_client_context
from substrate_data.files.service import sweep_files
from substrate_data.sql.planner import Planner
from substrate_data.tables.credentials import CredentialVendor
from substrate_data.tables.iceberg import TableService
from substrate_data.tables.staging import Staging

logger = logging.getLogger(__name__)

API_VERSION = "1.0.0"


def build_services(
    settings: Settings,
    *,
    verifier: TokenVerifier | None = None,
    tables: TableService | None = None,
    controller_transport: httpx.AsyncBaseTransport | None = None,
    worker_transport: httpx.AsyncBaseTransport | None = None,
) -> Services:
    if settings.ticket_private_key_file is None:
        raise ValueError("The control plane requires SUBSTRATE_DATA_TICKET_PRIVATE_KEY_FILE")
    store = CatalogStore(
        settings.metadata,
        url=settings.metadata_url.get_secret_value(),
        pool_size=settings.metadata_pool_size,
    )
    store.ensure_default(settings.admin_group)
    tables = tables or TableService(settings)
    signer = TicketSigner(settings.ticket_private_key_file, settings.ticket_ttl_seconds)
    return Services(
        settings=settings,
        store=store,
        tables=tables,
        staging=Staging(settings),
        planner=Planner(
            store,
            tables,
            signer,
            settings.mask_salt.get_secret_value(),
            CredentialVendor(settings) if settings.storage_sts_role_arn else None,
        ),
        verifier=verifier or TokenVerifier(settings),
        controller=ControllerClient(settings, controller_transport),
        workers=httpx.AsyncClient(
            timeout=httpx.Timeout(10, read=settings.statement_timeout_seconds + 30),
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
            trust_env=False,
            verify=internal_client_context(settings),
            transport=worker_transport,
        ),
    )


async def enforce_retention(
    store: CatalogStore, days: int, every_seconds: float = 6 * 3600
) -> None:
    """Delete audit and statement history past the retention period, at startup and every few hours."""
    while days > 0:
        try:
            removed = await asyncio.to_thread(store.purge_history, days)
            if any(removed.values()):
                logger.info("Retention (%s days) removed %s", days, removed)
        except Exception:  # noqa: BLE001 - a failed purge must never take the API down
            logger.exception("Retention purge failed")
        await asyncio.sleep(every_seconds)


def create_app(settings: Settings | None = None, services: Services | None = None) -> FastAPI:
    settings = settings or Settings()
    svc = services or build_services(settings)
    publisher = Publisher(svc)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(publisher.run())
        retention = asyncio.create_task(
            enforce_retention(svc.store, settings.history_retention_days)
        )
        # Expires pending uploads and purges old file versions (docs/FILES.md).
        sweeper = asyncio.create_task(sweep_files(files.files_service_for(app)))
        try:
            yield
        finally:
            for background in (task, retention, sweeper):
                background.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await background
            await svc.controller.close()
            await svc.workers.aclose()
            if publisher.leader is not None:
                publisher.leader.release()
            svc.store.close()

    app = FastAPI(
        title="Substrate Data",
        version=API_VERSION,
        lifespan=lifespan,
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
        redoc_url=None,
    )
    app.state.services = svc
    app.state.publisher = publisher
    app.state.active = statements.ActiveStatements()
    app.state.limiter = RateLimiter(settings)
    http.install(app, settings)
    for module in (
        catalog,
        statements,
        views,
        materialized,
        maintenance,
        policies,
        imports,
        workloads,
        assets,
        files,
    ):
        app.include_router(module.router)

    @app.get("/livez", include_in_schema=False)
    def livez():
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz():
        with svc.store.connection() as db:
            db.execute("SELECT 1")
        try:
            await svc.controller.resources()
        except Problem:
            raise Problem(503, "not-ready", "Compute controller is unreachable") from None
        return {"status": "ready"}

    def openapi():
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=API_VERSION, routes=app.routes)
        schema.setdefault("components", {})["securitySchemes"] = {
            "oidc": {
                "type": "openIdConnect",
                "openIdConnectUrl": settings.oidc_issuer + "/.well-known/openid-configuration",
            }
        }
        schema["security"] = [{"oidc": []}]
        app.openapi_schema = schema
        return schema

    app.openapi = openapi
    return app
