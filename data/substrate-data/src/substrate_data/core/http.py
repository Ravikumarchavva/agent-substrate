"""Uniform HTTP behaviour for every service: request ids and RFC 9457 problem responses."""

from __future__ import annotations

import logging
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from substrate_data.core.errors import Problem, public_error
from substrate_data.core.models import Settings

logger = logging.getLogger(__name__)
PROBLEM = "application/problem+json"


def request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "") or request.headers.get("x-request-id", "")


def problem_response(exc: Problem, request: Request) -> JSONResponse:
    headers = {}
    if "retry_after" in exc.extensions:
        headers["Retry-After"] = str(exc.extensions["retry_after"])
    if exc.status == 401:
        headers["WWW-Authenticate"] = 'Bearer realm="substrate-data"'
    return JSONResponse(
        exc.body(request.url.path, request_id(request)),
        status_code=exc.status,
        media_type=PROBLEM,
        headers=headers,
    )


def install(app: FastAPI, settings: Settings) -> None:
    @app.middleware("http")
    async def correlate(request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        request.state.request_id = incoming if 8 <= len(incoming) <= 64 else uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(Problem)
    async def problem(request: Request, exc: Problem):
        return problem_response(exc, request)

    @app.exception_handler(RequestValidationError)
    async def validation(request: Request, exc: RequestValidationError):
        errors = [
            {"location": ".".join(map(str, e["loc"])), "message": e["msg"]}
            for e in exc.errors()[:20]
        ]
        return problem_response(
            Problem(400, "invalid-request", "The request is not valid", errors=errors), request
        )

    @app.exception_handler(ValueError)
    async def value_error(request: Request, exc: ValueError):
        return problem_response(
            Problem(400, "invalid-request", public_error(exc, settings)), request
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        code = {404: "not-found", 405: "method-not-allowed", 413: "payload-too-large"}.get(
            exc.status_code, "http-error"
        )
        return problem_response(Problem(exc.status_code, code, str(exc.detail)), request)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        logger.exception("Unhandled error", extra={"request_id": request_id(request)})
        return problem_response(
            Problem(500, "internal-error", "An internal error occurred; quote the request id"),
            request,
        )
