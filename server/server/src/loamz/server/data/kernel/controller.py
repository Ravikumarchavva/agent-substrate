"""The compute controller, as the control plane reaches it: an async client with a short-lived resource cache."""

from __future__ import annotations

import time
from typing import Any

import httpx

from loamz.server.data.kernel.errors import error_from_problem, unavailable
from loamz.server.data.kernel.settings import Settings
from loamz.server.data.kernel.tls import internal_client_context

RESOURCE_CACHE_SECONDS = 2.0


class ControllerClient:
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
            raise error_from_problem(
                response.status_code, body, "Compute controller request failed"
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
