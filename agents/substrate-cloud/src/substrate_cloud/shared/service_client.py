"""The HTTP client one service uses to call another, as the caller.

The caller's own bearer token is forwarded, so the callee verifies the same Keycloak token and applies the same tenant and user:
no service trusts an identity in a request body. A callee's problem+json (RFC 9457) comes back as the same :class:`Problem` so
the original status and detail reach the end user; an unreachable callee is a 502.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx2 as httpx

from substrate_cloud.shared.problem import Problem

logger = logging.getLogger(__name__)

API = "/api/v1"


class ServiceClient:
    def __init__(
        self,
        base_url: str,
        name: str,
        *,
        timeout: float = 10.0,
        transport: Any = None,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._http = httpx.AsyncClient(
            base_url=self.base_url, timeout=timeout, transport=transport
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    @staticmethod
    def headers(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"} if token else {}

    async def request(
        self, method: str, path: str, token: str, **kwargs: Any
    ) -> httpx.Response:
        try:
            response = await self._http.request(
                method, f"{API}{path}", headers=self.headers(token), **kwargs
            )
        except httpx.HTTPError as exc:
            logger.warning("%s service unreachable: %s", self.name, exc)
            raise Problem(
                502, "bad-gateway", f"The {self.name} service is unreachable"
            ) from exc
        if response.status_code >= 400:
            raise _problem_from(self.name, response)
        return response

    async def json(self, method: str, path: str, token: str, **kwargs: Any) -> Any:
        response = await self.request(method, path, token, **kwargs)
        return response.json() if response.content else None

    async def open_stream(self, path: str, token: str, **kwargs: Any) -> httpx.Response:
        """A GET whose body is read as it arrives (server-sent events). The caller closes the response; a refusal raises first."""
        # An event stream goes quiet between events: the read timeout bounds a request that never answers, not a stream.
        request = self._http.build_request(
            "GET",
            f"{API}{path}",
            headers=self.headers(token),
            timeout=httpx.Timeout(self._timeout, read=None),
            **kwargs,
        )
        try:
            response = await self._http.send(request, stream=True)
        except httpx.HTTPError as exc:
            logger.warning("%s service unreachable: %s", self.name, exc)
            raise Problem(
                502, "bad-gateway", f"The {self.name} service is unreachable"
            ) from exc
        if response.status_code >= 400:
            try:
                await response.aread()
                raise _problem_from(self.name, response)
            finally:
                await response.aclose()
        return response


def _problem_from(service: str, response: httpx.Response) -> Problem:
    """The callee's error document as a Problem; anything else as a 502."""
    try:
        body = response.json()
        status = int(body.get("status", response.status_code))
        code = str(body.get("type", "")).rsplit("/", 1)[-1] or "bad-gateway"
        detail = str(body.get("detail") or f"The {service} service refused the request")
        extensions = {
            k: v
            for k, v in body.items()
            if k not in {"type", "title", "status", "detail", "instance"}
        }
    except Exception:
        return Problem(
            502,
            "bad-gateway",
            f"The {service} service answered {response.status_code}",
        )
    if status >= 500 and status not in (502, 503):
        return Problem(502, "bad-gateway", f"The {service} service failed: {detail}")
    headers = (
        {"Retry-After": response.headers["retry-after"]}
        if "retry-after" in response.headers
        else None
    )
    return Problem(status, code, detail, headers=headers, **extensions)
