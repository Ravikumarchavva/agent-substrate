"""Everything a request handler needs, built once per process and stored on `app.state`."""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Any

import httpx
import orjson
import pyarrow as pa
from fastapi import Request

from substrate_data.auth.oidc import TokenVerifier
from substrate_data.auth.principal import Principal
from substrate_data.auth.scopes import route_declares_scope
from substrate_data.catalog.store import CatalogStore
from substrate_data.core.errors import Problem, unavailable
from substrate_data.core.models import Settings
from substrate_data.core.tls import internal_client_context
from substrate_data.sql.planner import Planner
from substrate_data.tables.iceberg import TableService
from substrate_data.tables.staging import Staging

RESOURCE_CACHE_SECONDS = 2.0
MAX_PAGE = 1000


class ControllerClient:
    """Async client for the compute controller, with a short-lived resource cache."""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.http = httpx.AsyncClient(
            base_url=settings.controller_url + "/internal/v1",
            headers={"Authorization": "Bearer " + settings.controller_token.get_secret_value()},
            timeout=10,
            trust_env=False,
            verify=internal_client_context(settings),
            transport=transport,
        )
        self.cached: tuple[float, list[dict[str, Any]]] | None = None

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self.http.request(method, path, **kwargs)
        except httpx.HTTPError:
            raise unavailable("The compute controller is unavailable") from None
        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = {}
            raise Problem(
                response.status_code if response.status_code < 500 else 502,
                body.get("type", "compute-error").rsplit("/", 1)[-1],
                body.get("detail", "Compute controller request failed"),
            )
        if method != "GET" and path.startswith("/resources"):
            self.cached = None
        return response.json()

    async def resources(self) -> list[dict[str, Any]]:
        if self.cached and time.monotonic() - self.cached[0] < RESOURCE_CACHE_SECONDS:
            return self.cached[1]
        resources = (await self.request("GET", "/resources"))["resources"]
        self.cached = (time.monotonic(), resources)
        return resources

    async def close(self) -> None:
        await self.http.aclose()


@dataclass
class Services:
    settings: Settings
    store: CatalogStore
    tables: TableService
    staging: Staging
    planner: Planner
    verifier: TokenVerifier
    controller: ControllerClient
    workers: httpx.AsyncClient


def services(request: Request) -> Services:
    return request.app.state.services


def principal(request: Request) -> Principal:
    """Authenticate every API request; the principal is cached on the request."""
    cached = getattr(request.state, "principal", None)
    if cached is not None:
        return cached
    value = request.app.state.services.verifier.verify(request.headers.get("authorization"))
    if value.read_only and not read_only_allowed(request):
        raise Problem(403, "token-read-only", "This token may read data but not change anything")
    if (
        value.scopes is not None
        and not value.read_only
        and not read_only_allowed(request)
        and not route_declares_scope(request)
    ):
        raise Problem(403, "insufficient-scope", "This token may not use this route")
    request.app.state.limiter.check(value.id)
    request.state.principal = value
    return value


def read_only_allowed(request: Request) -> bool:
    """A read-only token may browse and run statements (a POST that only reads)."""
    if request.method in {"GET", "HEAD"}:
        return True
    path = request.url.path
    return request.method == "POST" and (
        path == "/api/v1/statements"
        or (path.startswith("/api/v1/statements/") and path.endswith(":cancel"))
    )


def page_size(value: int | None) -> int:
    if value is None:
        return 100
    if not 1 <= value <= MAX_PAGE:
        raise Problem(400, "invalid-request", f"page_size must be between 1 and {MAX_PAGE}")
    return value


def encode_token(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def decode_token(token: str | None) -> str:
    if not token:
        return ""
    try:
        return base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
    except ValueError:
        raise Problem(400, "invalid-request", "Invalid page_token") from None


def arrow_json(table: pa.Table, limit: int | None) -> tuple[bytes, int, bool]:
    """Columnar result -> one orjson pass: {columns, rows (arrays), row_count, truncated}."""
    truncated = limit is not None and table.num_rows > limit
    if truncated:
        table = table.slice(0, limit)
    columns = [{"name": f.name, "type": str(f.type)} for f in table.schema]
    data = [column.to_pylist() for column in table.columns]
    rows = list(zip(*data)) if data else []
    body = {"columns": columns, "rows": rows, "row_count": table.num_rows, "truncated": truncated}
    return (
        orjson.dumps(body, default=str, option=orjson.OPT_SERIALIZE_NUMPY),
        table.num_rows,
        truncated,
    )
