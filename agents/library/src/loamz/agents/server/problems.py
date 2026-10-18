"""RFC 9457 problem documents for every error, from one table (`loamz.data.storage.errors.HTTP_STATUS`)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from loamz.data.storage.errors import LoamzError, http_status
from loamz.identity import IdentityError

PROBLEM_BASE = "https://loamz.com/problems/"
MEDIA_TYPE = "application/problem+json"
logger = logging.getLogger(__name__)

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


def problem(
    request: Request, status: int, code: str, detail: str, **extensions: Any
) -> JSONResponse:
    body = {
        "type": PROBLEM_BASE + code,
        "title": code.replace("-", " ").capitalize(),
        "status": status,
        "detail": detail,
        "instance": request.url.path,
        **extensions,
    }
    headers = (
        {"Retry-After": str(int(extensions["retry_after"]))}
        if "retry_after" in extensions
        else None
    )
    return JSONResponse(
        jsonable_encoder(body), status_code=status, media_type=MEDIA_TYPE, headers=headers
    )


def install(app: FastAPI) -> None:
    @app.exception_handler(LoamzError)
    async def _domain(request: Request, error: LoamzError) -> JSONResponse:
        return problem(request, http_status(error), error.code, str(error), **error.extensions())

    @app.exception_handler(IdentityError)
    async def _identity(request: Request, error: IdentityError) -> JSONResponse:
        response = problem(request, error.status, error.code, error.detail)
        if error.status == 401:
            response.headers["WWW-Authenticate"] = 'Bearer realm="loamz"'
        return response

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, error: RequestValidationError) -> JSONResponse:
        return problem(
            request, 422, "invalid-request", "The request is not valid.", errors=error.errors()
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, error: StarletteHTTPException) -> JSONResponse:
        return problem(
            request,
            error.status_code,
            _HTTP_CODES.get(error.status_code, "error"),
            str(error.detail),
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, error: Exception) -> JSONResponse:
        logger.exception("unhandled error on %s", request.url.path)
        return problem(request, 500, "internal-error", "Something went wrong on our side.")
