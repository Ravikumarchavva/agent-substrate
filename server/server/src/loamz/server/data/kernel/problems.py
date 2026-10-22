"""Uniform HTTP behaviour for every Loamz Data process: request ids, and RFC 9457 problem documents for every error.

A typed error (`loamz.engine.errors`) becomes a problem through the one status table (`HTTP_STATUS`); its
`type` is `https://loamz.com/problems/<code>`. Validation failures are `422 invalid-request` with `errors`; an
unexpected error is `500 internal-error` with nothing of its internals in the body (it is logged with the request id).
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from loamz.engine.errors import LoamzError, http_status
from loamz.server.data.kernel.errors import public_error
from loamz.server.data.kernel.settings import Settings
from loamz.server.identity import IdentityError

logger = logging.getLogger(__name__)
PROBLEM = "application/problem+json"
PROBLEM_BASE = "https://loamz.com/problems/"
_HTTP_CODES = {
    400: "invalid-request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not-found",
    405: "method-not-allowed",
    409: "conflict",
    413: "quota-exceeded",
    422: "invalid-request",
    429: "rate-limited",
    503: "unavailable",
}


def request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "") or request.headers.get("x-request-id", "")


def problem(
    request: Request, status: int, code: str, detail: str, **extensions: Any
) -> JSONResponse:
    headers = {}
    if extensions.get("retry_after") is not None:
        headers["Retry-After"] = str(max(1, round(float(extensions["retry_after"]))))
    if status == 401:
        headers["WWW-Authenticate"] = 'Bearer realm="loamz"'
    body = {
        "type": PROBLEM_BASE + code,
        "title": code.replace("-", " ").capitalize(),
        "status": status,
        "detail": detail,
        "instance": request.url.path,
        "request_id": request_id(request),
        **extensions,
    }
    return JSONResponse(body, status_code=status, media_type=PROBLEM, headers=headers)


def error_response(request: Request, error: LoamzError) -> JSONResponse:
    return problem(request, http_status(error), error.code, str(error), **error.extensions())


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

    @app.exception_handler(LoamzError)
    async def typed(request: Request, error: LoamzError):
        return error_response(request, error)

    @app.exception_handler(IdentityError)
    async def identity(request: Request, error: IdentityError):
        # One spelling for "no or bad credentials" across every Loamz error (`UnauthenticatedError`).
        code = "unauthenticated" if error.status == 401 else error.code
        return problem(request, error.status, code, error.detail)

    @app.exception_handler(RequestValidationError)
    async def validation(request: Request, error: RequestValidationError):
        errors = [
            {"location": ".".join(map(str, e["loc"])), "message": e["msg"]}
            for e in error.errors()[:20]
        ]
        return problem(request, 422, "invalid-request", "The request is not valid", errors=errors)

    @app.exception_handler(ValueError)
    async def value_error(request: Request, error: ValueError):
        return problem(request, 400, "invalid-request", public_error(error, settings))

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException):
        code = _HTTP_CODES.get(error.status_code, "error")
        return problem(request, error.status_code, code, str(error.detail))

    @app.exception_handler(Exception)
    async def unexpected(request: Request, error: Exception):
        logger.exception("Unhandled error", extra={"request_id": request_id(request)})
        return problem(
            request, 500, "internal-error", "An internal error occurred; quote the request id"
        )
