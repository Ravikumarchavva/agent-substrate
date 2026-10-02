"""Document extraction by URL — the client for a document-intelligence service, and the one place that decides
service-or-local.

Layout-aware extraction (OCR, charts and tables as images, reading order) needs heavy dependencies and often a GPU, so it
runs as a server of its own and this library only holds the *client*: ``ExtractionClient(base_url)``. The server is
``apps/document-intelligence`` in this repository, but anything that speaks the same two endpoints works, and nothing here
imports it. The wire shapes below are the contract both sides import.

``extract_document`` is what call sites use: the service when a URL is configured, else (or if it fails) the light local
``LocalDocumentExtractor`` — a PDF text layer through pdfplumber/pypdf, with Tesseract for scanned pages if the ``ocr``
extra is installed. Office formats (DOCX, PPTX, …) need the service; without one they come back as an empty result rather
than an error, the way every extractor here reports "nothing I can read".
"""

from __future__ import annotations

import base64
import logging
import mimetypes

from typing import Any

import httpx2 as httpx
from pydantic import BaseModel

from substrate.documents import ExtractedImage as ExtractedImageDTO
from substrate.documents import ExtractedPage, ExtractionResult
from substrate.integrations.llm.endpoint import InferenceEndpoint

logger = logging.getLogger(__name__)

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
    what integrations/knowledge/citations.py needs for page-accurate
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

    def _timeout_for(self, timeout_s: float | None) -> httpx.Timeout:
        """The constructor timeout, with just the read leg overridden when
        the caller passes one — e.g. a batch driver that knows a batch's
        total page count and wants a bigger read budget for this one call
        without touching connect/write/pool."""
        if timeout_s is None:
            return self._timeout
        return httpx.Timeout(
            connect=self._timeout.connect,
            read=timeout_s,
            write=self._timeout.write,
            pool=self._timeout.pool,
        )

    async def extract(
        self,
        data: bytes,
        filename: str,
        content_type: str,
        *,
        timeout_s: float | None = None,
    ) -> ExtractResponse:
        """Extract text + chart/table images from a document. Never raises —
        a failure of any kind (bad status, connection error, timeout) comes
        back as ``success=False`` so the caller can fall back to a lighter
        local extractor instead of failing the whole chat turn.

        ``timeout_s`` overrides the client's own constructor timeout for
        this call only (httpx supports a per-request ``timeout=`` override).
        """
        try:
            resp = await self._request(
                "POST",
                "/v1/extract",
                json={
                    "content_base64": base64.b64encode(data).decode("ascii"),
                    "filename": filename,
                    "content_type": content_type,
                },
                timeout=self._timeout_for(timeout_s),
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
        self,
        items: list[tuple[bytes, str, str]],
        *,
        timeout_s: float | None = None,
    ) -> list[ExtractResponse]:
        """Extract multiple documents in ONE request — see
        document_intelligence/service/pipeline.py::ExtractionPipeline.
        extract_batch's docstring for why this beats N sequential
        ``extract()`` calls (real GPU-batching headroom a single document's
        pages often can't fill on their own). ``items`` is
        ``(data, filename, content_type)`` tuples, same fields as
        ``extract()`` batched. Results come back in the same order as
        ``items``.

        ``timeout_s`` overrides the client's own constructor timeout for
        this call only — a caller that batches by page-count budget (see
        ``pdfqa_rag.pipeline.dataset_ingest_gpu``) can size this to the
        batch's actual total page count instead of one fixed timeout shared
        across every batch regardless of size, which was a real, measured
        cause of avoidable failures on large documents.

        Never raises — a transport-level failure (bad status, connection
        error, timeout) affects the whole batch the same way ``extract()``
        fails a single file: every item comes back ``success=False`` with
        the same error, so the caller can retry those files individually
        rather than losing the whole batch's worth of work silently.
        """
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
                timeout=self._timeout_for(timeout_s),
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


# Content types a document service converts to text (Office formats): without one, they cannot be read.
OFFICE_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/ms-powerpoint",
        "application/vnd.ms-powerpoint",
        "application/vnd.oasis.opendocument.text",
        "application/rtf",
        "text/rtf",
    }
)


def _response_to_result(resp: ExtractResponse) -> ExtractionResult:
    """The wire response (base64 images, a page list) as the engine's own ``ExtractionResult``."""
    pages = [ExtractedPage(page_number=p.page_number, text=p.text, markdown=p.markdown) for p in resp.pages]
    by_number = {p.page_number: p for p in pages}
    for img in resp.images:
        page = by_number.get(img.page_number)
        if page is None:
            continue
        page.images.append(
            ExtractedImageDTO(
                data=base64.b64decode(img.data_base64),
                media_type=img.media_type,
                page_number=img.page_number,
                label=img.label,
                confidence=img.confidence,
                caption=img.caption,
                id=img.id,
            )
        )
    return ExtractionResult(pages=pages, markdown=resp.markdown, engine=resp.engine)


async def extract_document(
    data: bytes,
    filename: str,
    content_type: str,
    *,
    endpoint: InferenceEndpoint | None = None,
) -> ExtractionResult:
    """Extract text (and, from a service, chart/table images) from a document.

    With ``endpoint`` (the service's URL) the service goes first; if it is unreachable or reports a failure, a PDF falls
    back to the local extractor. Never raises for a normal extraction failure — a corrupt file, a service that is down, a
    format nothing here can read — it returns an ``ExtractionResult`` with no pages.
    """
    if endpoint is not None and endpoint.base_url:
        client = ExtractionClient(base_url=endpoint.base_url, auth_token=endpoint.api_key, timeout_s=endpoint.timeout_s)
        try:
            resp = await client.extract(data, filename, content_type, timeout_s=endpoint.timeout_s)
        finally:
            await client.close()
        if resp.success:
            return _response_to_result(resp)
        logger.warning("document service extraction failed for %r (%s) — trying local extraction", filename, resp.error)

    if content_type == "application/pdf" or filename.lower().endswith(".pdf"):
        from substrate.integrations.document.local_extractor import LocalDocumentExtractor

        return await LocalDocumentExtractor().extract(data, filename)
    return ExtractionResult(pages=[], markdown="", engine="none")


class ServiceBackedDocumentExtractor:
    """A ``DocumentExtractor`` over ``extract_document``: the document service at ``endpoint`` when there is one, the local
    extractor otherwise. Hand it anywhere a ``DocumentExtractor`` is taken (``PDFLoader(extractor=...)``)."""

    def __init__(self, *, endpoint: InferenceEndpoint | None = None) -> None:
        self._endpoint = endpoint

    async def extract(self, data: bytes, filename: str) -> ExtractionResult:
        content_type, _ = mimetypes.guess_type(filename)
        return await extract_document(data, filename, content_type or "application/octet-stream", endpoint=self._endpoint)


__all__ = [
    "OFFICE_CONTENT_TYPES",
    "ExtractResponse",
    "ExtractedImage",
    "ExtractedPageText",
    "ExtractionClient",
    "ServiceBackedDocumentExtractor",
    "extract_document",
]
