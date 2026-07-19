"""HTTP client for the Docling extraction service.

Used by chat_context.py to get structure-aware document text (tables,
layout, OCR) without loading Docling's ~4GB torch/CUDA runtime into the
main API process — see serving/services/docling/ for the service itself.

Single-URL only (no consistent-hash routing): extraction is stateless
request/response, so one low-replica service is enough and there's no
session affinity to route on.
"""

from __future__ import annotations
from substrate.logger import setup_logging

from typing import Any

import httpx
from pydantic import BaseModel

logger = setup_logging()

_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=90.0, write=10.0, pool=5.0)


class DoclingExtractResponse(BaseModel):
    """Response shape shared with serving/services/docling/schemas.py
    (re-exported there, mirroring the code_interpreter service's pattern —
    this module is the single source of truth for the wire shape)."""

    success: bool
    text: str = ""
    engine: str = "docling"
    page_count: int = 0
    error: str | None = None


class DoclingClient:
    """Async HTTP client for the docling extraction service."""

    def __init__(
        self,
        base_url: str = "http://docling:8080",
        auth_token: str = "",
        timeout_s: float = 90.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers: dict[str, str] = {}
        if auth_token:
            self._headers["Authorization"] = f"Bearer {auth_token}"
        self._timeout = httpx.Timeout(connect=5.0, read=timeout_s, write=10.0, pool=5.0)
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                headers=self._headers,
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
        return self._client

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        client = self._get_client()
        resp = await client.request(method, path, **kwargs)
        resp.raise_for_status()
        return resp

    async def extract(
        self, data: bytes, filename: str, content_type: str
    ) -> DoclingExtractResponse:
        """Extract text from a document. Never raises — a failure of any
        kind (bad status, connection error, timeout) comes back as
        ``success=False`` so the caller can fall back to a lighter local
        extractor instead of failing the whole chat turn."""
        import base64

        try:
            resp = await self._request(
                "POST",
                "/v1/extract",
                json={
                    "content_base64": base64.b64encode(data).decode("ascii"),
                    "filename": filename,
                    "content_type": content_type,
                },
            )
            return DoclingExtractResponse(**resp.json())
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Docling extract HTTP %d: %s",
                exc.response.status_code,
                exc.response.text[:500],
            )
            return DoclingExtractResponse(
                success=False,
                error=f"Service error {exc.response.status_code}: {exc.response.text[:200]}",
            )
        except httpx.TimeoutException as exc:
            logger.warning("Docling extract timed out for %r: %s", filename, exc)
            return DoclingExtractResponse(success=False, error=f"Timeout: {exc}")
        except httpx.RequestError as exc:
            logger.error("Docling extract connection error: %s", exc)
            return DoclingExtractResponse(
                success=False, error=f"Connection error: {exc}"
            )

    async def health(self) -> bool:
        try:
            resp = await self._request("GET", "/v1/health")
            return resp.status_code == 200
        except httpx.RequestError:
            return False

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
