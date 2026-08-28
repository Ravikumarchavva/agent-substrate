"""HTTP client for the document-extraction service.

Used by chat_context.py and LocalRagBackend to get layout-aware document
text and chart/table images, without loading paddlepaddle into the main API
process — see document_intelligence/service/ for the service itself.
Multimodal embedding + reranking is a separate concern/service now — see
substrate.runtimes.embedding_reranker.client.EmbeddingRerankerClient.

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
    document_intelligence/service/pipeline.py::ExtractionPipeline."""

    data_base64: str
    media_type: str = "image/png"
    page_number: int | None = None
    label: str = "chart"
    confidence: float = 0.0
    # OCR'd text for this block, already computed by the same layout pass —
    # see ExtractionPipeline.extract(). Kept so lexical/exact-text search can
    # still find a confident chart/table, not only visual similarity search.
    caption: str | None = None
    # Stable id (e.g. "img-p3-0") cross-referenced by ExtractedPageText.markdown
    # / ExtractResponse.markdown's "cid:{id}" image links — see pipeline.py.
    id: str = ""


class ExtractedPageText(BaseModel):
    """One page's plain text — kept page-separated (not pre-joined) so
    callers like LocalRagBackend can build one Document per page, which is
    what capabilities/knowledge/citations.py needs for page-accurate
    citations."""

    page_number: int
    text: str
    # This page's PaddleX-native markdown — real reading order, images
    # embedded inline via "cid:{id}" links (resolve against ExtractResponse.
    # images), tables as HTML. Faithful/human-readable rendering; `text`
    # above stays the plain-text stream used for embeddings/lexical search.
    markdown: str = ""


class ExtractResponse(BaseModel):
    """Response shape shared with document_intelligence/service/schemas.py
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
    # Whole-document markdown (pages joined via PaddleX's CJK-aware
    # concatenate_markdown_pages) — see ExtractedPageText.markdown.
    markdown: str = ""


class ExtractionClient:
    """Async HTTP client for the document-extraction service."""

    def __init__(
        self,
        base_url: str = "http://document-intelligence:8080",
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

    async def extract_batch(
        self, items: list[tuple[bytes, str, str]]
    ) -> list[ExtractResponse]:
        """Extract multiple documents in ONE request — see
        document_intelligence/service/pipeline.py::ExtractionPipeline.
        extract_batch's docstring for why this beats N sequential
        ``extract()`` calls (real GPU-batching headroom a single document's
        pages often can't fill on their own). ``items`` is
        ``(data, filename, content_type)`` tuples, same fields as
        ``extract()`` batched. Results come back in the same order as
        ``items``.

        Never raises — a transport-level failure (bad status, connection
        error, timeout) affects the whole batch the same way ``extract()``
        fails a single file: every item comes back ``success=False`` with
        the same error, so the caller can retry those files individually
        rather than losing the whole batch's worth of work silently.
        """
        import base64

        if not items:
            return []
        try:
            resp = await self._request(
                "POST",
                "/v1/extract-batch",
                json={
                    "items": [
                        {
                            "content_base64": base64.b64encode(data).decode("ascii"),
                            "filename": filename,
                            "content_type": content_type,
                        }
                        for data, filename, content_type in items
                    ]
                },
            )
            return [ExtractResponse(**item) for item in resp.json()]
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Extract-batch HTTP %d: %s",
                exc.response.status_code,
                exc.response.text[:500],
            )
            error = (
                f"Service error {exc.response.status_code}: {exc.response.text[:200]}"
            )
        except httpx.TimeoutException as exc:
            logger.warning(
                "Extract-batch timed out for %d file(s): %s", len(items), exc
            )
            error = f"Timeout: {exc}"
        except httpx.RequestError as exc:
            logger.error("Extract-batch connection error: %s", exc)
            error = f"Connection error: {exc}"
        return [ExtractResponse(success=False, error=error) for _ in items]

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
