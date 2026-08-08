"""HTTP client for the document-extraction service.

Used by chat_context.py to get layout-aware document text and chart/table
images, and by LocalRagBackend for multimodal embedding + reranking, without
loading paddlepaddle/torch into the main API process — see
serving/services/extraction/ for the service itself.

Single-URL only (no consistent-hash routing): every endpoint here is
stateless request/response, so one low-replica service is enough and there's
no session affinity to route on.
"""

from __future__ import annotations
from substrate.logger import setup_logging

from typing import Any

import httpx
from pydantic import BaseModel

logger = setup_logging()

_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=90.0, write=10.0, pool=5.0)


class ExtractedImage(BaseModel):
    """A chart/table/figure region cropped from a page — see
    serving/services/extraction/pipeline.py::ExtractionPipeline."""

    data_base64: str
    media_type: str = "image/png"
    page_number: int | None = None
    label: str = "chart"
    confidence: float = 0.0


class ExtractedPageText(BaseModel):
    """One page's plain text — kept page-separated (not pre-joined) so
    callers like LocalRagBackend can build one Document per page, which is
    what capabilities/knowledge/citations.py needs for page-accurate
    citations."""

    page_number: int
    text: str


class ExtractResponse(BaseModel):
    """Response shape shared with serving/services/extraction/schemas.py
    (re-exported there, mirroring the code_interpreter service's pattern —
    this module is the single source of truth for the wire shape)."""

    success: bool
    text: str = (
        ""  # all pages joined — convenience for callers that don't need page boundaries
    )
    pages: list[ExtractedPageText] = []
    images: list[ExtractedImage] = []
    engine: str = "paddleocr"
    page_count: int = 0
    error: str | None = None


class ExtractionClient:
    """Async HTTP client for the document-extraction service."""

    def __init__(
        self,
        base_url: str = "http://extraction:8080",
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
    ) -> ExtractResponse:
        """Extract text + chart/table images from a document. Never raises —
        a failure of any kind (bad status, connection error, timeout) comes
        back as ``success=False`` so the caller can fall back to a lighter
        local extractor instead of failing the whole chat turn."""
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
            return ExtractResponse(**resp.json())
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Extract HTTP %d: %s",
                exc.response.status_code,
                exc.response.text[:500],
            )
            return ExtractResponse(
                success=False,
                error=f"Service error {exc.response.status_code}: {exc.response.text[:200]}",
            )
        except httpx.TimeoutException as exc:
            logger.warning("Extract timed out for %r: %s", filename, exc)
            return ExtractResponse(success=False, error=f"Timeout: {exc}")
        except httpx.RequestError as exc:
            logger.error("Extract connection error: %s", exc)
            return ExtractResponse(success=False, error=f"Connection error: {exc}")

    async def embed_image(self, data: bytes) -> list[float] | None:
        """Embed a chart/table image. Returns ``None`` on any failure —
        callers should skip indexing that one image, not fail the whole
        ingest."""
        import base64

        try:
            resp = await self._request(
                "POST",
                "/v1/embed",
                json={"image_base64": base64.b64encode(data).decode("ascii")},
            )
            return resp.json()["embedding"]
        except (httpx.HTTPError, KeyError) as exc:
            logger.warning("embed_image failed: %s", exc)
            return None

    async def embed_text(self, text: str) -> list[float] | None:
        """Embed a text query into the same space as ``embed_image`` (used
        to search the image collection). Returns ``None`` on any failure."""
        try:
            resp = await self._request("POST", "/v1/embed", json={"text": text})
            return resp.json()["embedding"]
        except (httpx.HTTPError, KeyError) as exc:
            logger.warning("embed_text failed: %s", exc)
            return None

    async def rerank(self, query: str, passages: list[str]) -> list[float] | None:
        """Score each passage's relevance to *query*, same order as input.
        Returns ``None`` on any failure — callers should fall back to the
        unreranked order, not fail the whole query."""
        if not passages:
            return []
        try:
            resp = await self._request(
                "POST", "/v1/rerank", json={"query": query, "passages": passages}
            )
            return resp.json()["scores"]
        except (httpx.HTTPError, KeyError) as exc:
            logger.warning("rerank failed: %s", exc)
            return None

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
