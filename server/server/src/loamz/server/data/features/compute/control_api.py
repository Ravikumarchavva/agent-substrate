"""Compute controller API (`/internal/v1`). Callable only by the control plane with its secret."""

from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Query, Request

from loamz.engine.errors import (
    InvalidRequestError,
    ServiceUnavailableError,
    UnauthenticatedError,
)
from loamz.server.data.features.compute.controller import ComputeController
from loamz.server.data.features.compute.models import JobRequest, ResourceSpec
from loamz.server.data.kernel import problems as http
from loamz.server.data.kernel.errors import conflict, not_found
from loamz.server.data.kernel.settings import Settings


def create_control_app(settings: Settings) -> FastAPI:
    token = settings.controller_token.get_secret_value()
    if len(token) < 32:
        raise ValueError("The controller requires LOAMZ_DATA_CONTROLLER_TOKEN (32+ characters)")
    controller = ComputeController(settings)

    @asynccontextmanager
    async def lifespan(app):
        controller.start()
        try:
            yield
        finally:
            controller.close()

    app = FastAPI(
        title="Loamz Data compute controller", lifespan=lifespan, docs_url=None, redoc_url=None
    )
    app.state.controller = controller
    http.install(app, settings)

    @app.middleware("http")
    async def standby(request: Request, call_next):
        """Only the elected controller serves; a load balancer's readiness probe finds it."""
        if request.url.path.startswith("/internal/") and not controller.active:
            return http.error_response(
                request, ServiceUnavailableError("This controller is on standby", retry_after=1)
            )
        return await call_next(request)

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        if request.url.path.startswith("/internal/") and not secrets.compare_digest(
            request.headers.get("authorization", ""), "Bearer " + token
        ):
            return http.error_response(
                request, UnauthenticatedError("Unauthorized controller request")
            )
        return await call_next(request)

    @app.exception_handler(KeyError)
    async def missing(request: Request, exc: KeyError):
        return http.error_response(request, not_found("Compute record"))

    @app.get("/livez")
    def livez():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz():
        alive = controller.thread is not None and controller.thread.is_alive()
        if not controller.active or not alive:
            raise ServiceUnavailableError("Not the active scheduler")
        return {"status": "ready", "storage_id": controller.identity}

    @app.get("/internal/v1/resources")
    def resources():
        return {"resources": controller.resources()}

    @app.post("/internal/v1/resources", status_code=201)
    def attach(body: ResourceSpec):
        if not body.token:
            raise InvalidRequestError("A connection token is required to attach compute")
        with controller.lock:
            if len(controller.store.resources()) >= 100:
                raise conflict("Resource limit reached")
            if any(r["name"] == body.name for r in controller.store.resources()):
                raise conflict(f"A resource named {body.name} already exists")
            resource_id = controller.store.create_resource(body)["id"]
        controller.wake.set()
        return next(r for r in controller.resources() if r["id"] == resource_id)

    @app.put("/internal/v1/resources/{resource_id}")
    def update(resource_id: str, body: ResourceSpec):
        with controller.lock:
            current = controller.store.resource(resource_id)
            if current["desired"] != "stopped":
                raise conflict("Pause the resource before changing its configuration")
            controller.store.save_resource({**current, **body.model_dump(exclude={"token", "url"})})
            controller.reattach(resource_id, body.url, body.token)
            controller.store.event(resource_id, "Configuration updated")
        return next(r for r in controller.resources() if r["id"] == resource_id)

    @app.post("/internal/v1/resources/{resource_id}:{action}")
    def action(resource_id: str, action: Literal["start", "stop", "delete"]):
        return controller.action(resource_id, action)

    @app.get("/internal/v1/resources/{resource_id}/events")
    def events(resource_id: str):
        controller.store.resource(resource_id)
        return {"events": controller.store.history(resource_id=resource_id)}

    @app.post("/internal/v1/jobs", status_code=202)
    def submit(body: JobRequest):
        return controller.submit(body)

    @app.get("/internal/v1/jobs")
    def jobs(limit: int = Query(100, ge=1, le=1000)):
        return {"jobs": controller.store.jobs(limit=limit)}

    @app.get("/internal/v1/jobs/{job_id}")
    def job(job_id: str):
        return controller.store.job(job_id)

    @app.get("/internal/v1/jobs/{job_id}/attempts")
    def attempts(job_id: str):
        controller.store.job(job_id)
        return {"attempts": controller.store.history(job_id=job_id)}

    @app.post("/internal/v1/jobs/{job_id}:cancel")
    def cancel(job_id: str):
        return controller.cancel(job_id)

    return app
