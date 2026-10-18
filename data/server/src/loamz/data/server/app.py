"""create_app: the Loamz Data control plane, one FastAPI app serving the features its roles select.

Every `/api/v1` route requires an OIDC access token. The control plane never reads table data: it plans, authorizes
and signs; attached compute reads and writes object storage directly.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from loamz.data.server.features import FEATURES, ROLES
from loamz.data.server.features.files.service import files_service, sweep_files
from loamz.data.server.features.tables.publications import job_publisher
from loamz.data.server.kernel import problems
from loamz.data.server.kernel.container import Container
from loamz.data.server.kernel.metadata import CatalogStore
from loamz.data.server.kernel.settings import Settings
from loamz.data.storage.errors import LoamzError, ServiceUnavailableError

logger = logging.getLogger(__name__)

API_VERSION = "1.0.0"


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


def create_app(container: Container | None = None, *, roles: set[str] | None = None) -> FastAPI:
    """`roles` defaults to the settings' roles; `{"all"}` mounts every feature."""
    container = container or Container(Settings())
    settings = container.settings
    wanted = roles or set(settings.roles)
    unknown = wanted - ROLES - {"all"}
    if unknown:
        raise ValueError(f"unknown roles {sorted(unknown)}; known: {sorted(ROLES)}")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = container.store
        publisher = container.get(job_publisher)
        background = [
            asyncio.create_task(publisher.run()),
            asyncio.create_task(enforce_retention(store, settings.history_retention_days)),
            # Expires pending uploads and purges old file versions (docs/FILES.md).
            asyncio.create_task(sweep_files(container.get(files_service))),
        ]
        try:
            yield
        finally:
            for task in background:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            if publisher.leader is not None:
                publisher.leader.release()
            await container.aclose()

    app = FastAPI(
        title="Loamz Data",
        version=API_VERSION,
        lifespan=lifespan,
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
        redoc_url=None,
    )
    app.state.container = container
    problems.install(app, settings)
    for feature in FEATURES:
        if "all" in wanted or feature.role in wanted:
            app.include_router(feature.router())

    @app.get("/livez", include_in_schema=False)
    def livez():
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz():
        with container.store.connection() as db:
            db.execute("SELECT 1")
        try:
            await container.controller.resources()
        except LoamzError:
            raise ServiceUnavailableError("Compute controller is unreachable") from None
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
