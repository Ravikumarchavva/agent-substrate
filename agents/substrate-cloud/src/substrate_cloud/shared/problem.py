"""RFC 9457 problem details for every error the platform returns (``application/problem+json``).

One handler set, installed on the monolith and on every microservice by :func:`install_problem_handlers`: a route raises
``HTTPException`` or :class:`Problem` as before and the client always receives the same document shape
(``type``, ``title``, ``status``, ``detail``, ``instance``, plus extensions).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from substrate_identity import IdentityError

PROBLEM_BASE = "https://substrate.dev/problems/"
MEDIA_TYPE = "application/problem+json"

_CODES = {
    400: "invalid-request",
    401: "unauthorized",
    403: "forbidden",
    404: "not-found",
    405: "method-not-allowed",
    409: "conflict",
    413: "payload-too-large",
    415: "unsupported-media-type",
    422: "invalid-request",
    423: "locked",
    429: "rate-limited",
    500: "internal-error",
    502: "bad-gateway",
    503: "unavailable",
}


class Problem(Exception):
    """An error with a stable machine-readable ``code``."""

    def __init__(
        self,
        status: int,
        code: str,
        detail: str,
        *,
        headers: dict[str, str] | None = None,
        **extensions: Any,
    ):
        super().__init__(detail)
        self.status, self.code, self.detail = status, code, detail
        self.headers, self.extensions = headers, extensions


def problem_response(
    request: Request,
    status: int,
    code: str,
    detail: str,
    *,
    headers: dict[str, str] | None = None,
    **extensions: Any,
) -> JSONResponse:
    body = {
        "type": PROBLEM_BASE + code,
        "title": code.replace("-", " ").capitalize(),
        "status": status,
        "detail": detail,
        "instance": request.url.path,
        **extensions,
    }
    return JSONResponse(
        body, status_code=status, headers=headers, media_type=MEDIA_TYPE
    )


def install_problem_handlers(app: FastAPI) -> None:
    @app.exception_handler(Problem)
    async def _problem(request: Request, error: Problem) -> JSONResponse:
        return problem_response(
            request,
            error.status,
            error.code,
            error.detail,
            headers=error.headers,
            **error.extensions,
        )

    @app.exception_handler(IdentityError)
    async def _identity(request: Request, error: IdentityError) -> JSONResponse:
        headers = (
            {"WWW-Authenticate": 'Bearer realm="substrate"'}
            if error.status == 401
            else None
        )
        return problem_response(
            request, error.status, error.code, error.detail, headers=headers
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, error: StarletteHTTPException) -> JSONResponse:
        code = _CODES.get(error.status_code, f"http-{error.status_code}")
        if isinstance(error.detail, str):
            return problem_response(
                request, error.status_code, code, error.detail, headers=error.headers
            )
        return problem_response(
            request,
            error.status_code,
            code,
            _CODES.get(error.status_code, "request failed").replace("-", " "),
            headers=error.headers,
            errors=jsonable_encoder(error.detail),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        return problem_response(
            request,
            422,
            "invalid-request",
            "The request does not match the schema",
            errors=jsonable_encoder(error.errors(), custom_encoder={Exception: str}),
        )
