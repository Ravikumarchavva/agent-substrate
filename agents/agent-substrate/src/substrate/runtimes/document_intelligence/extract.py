"""``extract_document`` — the ONE function owning the entire extraction
decision tree: service-vs-local dispatch, and the fallback chain between
them.

Real finding this collapses (see the phase-2 plan): three+ independent
reimplementations of "try the document-intelligence extraction service,
else fall back to pypdf" existed before this module —
``serving/monolith/routes/files.py::_build_extracted_sidecar_text``,
``serving/monolith/routes/chat_context.py::_extract_document_text``, and
``capabilities/knowledge/backends/local.py::LocalRagBackend._load``. Every
one of them now calls this instead.

Placed inside ``runtimes/`` deliberately: ``runtimes/`` imports only
``substrate.kernel``/``substrate.logger`` today (verified via grep), and
``pyproject.toml`` already documents it as exempt from the "serving cannot
import agents/capabilities" import-linter contract — this needs zero new
lint exceptions, while a ``capabilities/knowledge/`` home would need two.
``capabilities/`` already imports ``runtimes/`` today (``local.py`` imports
both ``document_intelligence.client`` and ``embedding_reranker.client``),
so the dependency direction is already blessed, not new.

``endpoint`` reuses Phase 0's ``InferenceEndpoint`` — the same config
vocabulary every model-serving seam in this codebase speaks — as *where
the document-intelligence service lives*, not just where a VL model
lives. ``endpoint=None`` means "no service configured"; local-only
extraction runs unconditionally in that case, matching every call site's
existing behavior when ``DOCUMENT_INTELLIGENCE_SERVICE_URL`` is unset.
"""

from __future__ import annotations

import asyncio
import base64
import mimetypes

from substrate.integrations.llm.endpoint import InferenceEndpoint
from substrate.kernel.document import ExtractedImage, ExtractedPage, ExtractionResult
from substrate.logger import setup_logging
from substrate.runtimes.document_intelligence.client import ExtractionClient, ExtractResponse
from substrate.runtimes.document_intelligence.service.engines.raw_text import RawTextEngine

logger = setup_logging("substrate.document_intelligence.extract")

# RawTextEngine holds no per-instance state (no worker pool, no open
# model — see its own docstring) -- one shared instance is safe and avoids
# rebuilding it per call.
_local_engine = RawTextEngine()


def _response_to_result(resp: ExtractResponse) -> ExtractionResult:
    """Reshape the wire ``ExtractResponse`` (base64 images, page list) into
    the internal ``ExtractionResult`` DTO every engine already speaks --
    the one place that conversion happens, instead of each call site
    re-deriving it."""
    pages = [
        ExtractedPage(page_number=p.page_number, text=p.text, markdown=p.markdown)
        for p in resp.pages
    ]
    pages_by_number = {p.page_number: p for p in pages}
    for img in resp.images:
        page = pages_by_number.get(img.page_number)
        if page is None:
            continue
        page.images.append(
            ExtractedImage(
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
    """Extract text/images from a document, trying the document-
    intelligence service first (if ``endpoint`` is configured) and falling
    back to local, in-process extraction (``RawTextEngine`` — pypdfium2 for
    PDF, markitdown/LibreOffice for Office formats) when the service is
    unconfigured, unreachable, or returns a failure.

    Never raises for a normal extraction failure (a corrupt file, an
    unreachable service, a scanned/empty document) -- callers get back an
    ``ExtractionResult`` with empty pages rather than an exception,
    matching every engine's own "never raise per-document" contract.
    """
    if endpoint is not None and endpoint.base_url:
        client = ExtractionClient(
            base_url=endpoint.base_url,
            auth_token=endpoint.api_key,
            timeout_s=endpoint.timeout_s,
        )
        resp = await client.extract(
            data, filename, content_type, timeout_s=endpoint.timeout_s
        )
        if resp.success:
            return _response_to_result(resp)
        logger.warning(
            "document-intelligence service extraction failed for %r (%s) -- "
            "falling back to local raw_text extraction",
            filename,
            resp.error,
        )

    if not _local_engine.accepts(filename, content_type):
        return ExtractionResult(pages=[], markdown="", engine=_local_engine.name)
    return await asyncio.to_thread(_local_engine.extract, data, filename)


class ServiceBackedDocumentExtractor:
    """Kernel ``DocumentExtractor`` adapter over ``extract_document()`` —
    the document-intelligence service (PaddleOCR/PPStructureV3) when
    *endpoint* is configured, falling back to local raw-text extraction
    otherwise. Construct one of these and hand it anywhere a
    ``DocumentExtractor`` is expected (e.g. ``PDFLoader(extractor=...)``) —
    a drop-in, stronger alternative to ``agents.document.LocalDocumentExtractor``,
    the same role an MCP tool adapter plays for the kernel ``Tool`` Protocol.
    """

    def __init__(self, *, endpoint: InferenceEndpoint | None = None) -> None:
        self._endpoint = endpoint

    async def extract(self, data: bytes, filename: str) -> ExtractionResult:
        content_type, _ = mimetypes.guess_type(filename)
        return await extract_document(
            data, filename, content_type or "application/octet-stream",
            endpoint=self._endpoint,
        )


__all__ = ["extract_document", "ServiceBackedDocumentExtractor"]
