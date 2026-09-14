"""REST endpoints for the document-intelligence service.

All endpoints are prefixed with ``/v1/``.
Authentication is via ``Bearer <token>`` header (optional, configurable).
"""

from __future__ import annotations
from substrate.logger import setup_logging

import asyncio
import base64
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from .engines.base import DeclarativeExtractionEngine, ExtractionEngine, PaginatedExtractionEngine
from .schemas import (
    ExtractBatchRequest,
    ExtractedImage,
    ExtractedPageText,
    ExtractRequest,
    ExtractResponse,
    HealthResponse,
)

logger = setup_logging()

router = APIRouter(prefix="/v1", tags=["document-intelligence"])


async def _verify_token(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    """Validate Bearer token if DOCUMENT_INTELLIGENCE_AUTH_TOKEN is configured."""
    token = request.app.state.config.auth_token
    if not token:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")
    if authorization.removeprefix("Bearer ") != token:
        raise HTTPException(403, "Invalid token")


Authed = Annotated[None, Depends(_verify_token)]


async def _run_engine(engine: ExtractionEngine, data: bytes, filename: str):
    """Dispatch to whichever extraction shape this engine implements —
    ``routes.py`` never hardcodes which concrete engine is running, only
    which of the two ``ExtractionEngine`` Protocols it satisfies. Paginated
    engines are already async (they may await a pooled worker); declarative
    engines are sync library calls run off the event loop so one slow
    extraction doesn't stall every other request this service is handling."""
    if isinstance(engine, PaginatedExtractionEngine):
        return await engine.aextract(data, filename)
    assert isinstance(engine, DeclarativeExtractionEngine)
    return await asyncio.to_thread(engine.extract, data, filename)


async def _run_engine_batch(engine: ExtractionEngine, items: list[tuple[bytes, str]]):
    if isinstance(engine, PaginatedExtractionEngine):
        return await engine.aextract_batch(items)
    assert isinstance(engine, DeclarativeExtractionEngine)
    return await asyncio.to_thread(engine.extract_batch, items)


@router.post("/extract", response_model=ExtractResponse)
async def extract(body: ExtractRequest, request: Request, _: Authed):
    """Extract layout-aware text + chart/table images from a document."""
    engine: ExtractionEngine = request.app.state.engine
    if not engine.accepts(body.filename, body.content_type):
        raise HTTPException(
            400,
            f"Unsupported content_type {body.content_type!r} for "
            f"filename {body.filename!r}. Supported by the running "
            f"{engine.name!r} engine: {sorted(engine.supported_formats())}",
        )

    cfg = request.app.state.config
    try:
        data = base64.b64decode(body.content_base64, validate=True)
    except Exception as exc:
        raise HTTPException(400, f"Invalid base64 content: {exc}") from exc

    if len(data) > cfg.max_upload_bytes:
        raise HTTPException(
            413, f"File exceeds maximum size of {cfg.max_upload_bytes} bytes"
        )

    # Structural/security scan on the RAW bytes, before the parser touches
    # them — a hostile file must not get a chance to exploit the extraction
    # engine's own parsing first. See
    # runtimes/document_intelligence/security_scan.py for what's actually
    # verified working here (not just wired up).
    if getattr(cfg, "enable_document_security_scan", True):
        from substrate.kernel.agent.safety import Severity
        from substrate.runtimes.document_intelligence.security_scan import scan_document

        scan_verdict = await asyncio.to_thread(
            scan_document, data, filename=body.filename
        )
        if scan_verdict.flagged:
            logger.warning(
                "doc-firewall flagged %r (%s): %s",
                body.filename,
                scan_verdict.severity,
                scan_verdict.detail,
            )
        # Only HIGH/CRITICAL (doc-firewall's own BLOCK verdict — definitive
        # evidence) hard-rejects. MEDIUM (FLAG — review-worthy heuristics,
        # not confirmed malicious, see security_scan.py's _VERDICT_SEVERITY
        # comment) is logged above but doesn't stop extraction.
        if scan_verdict.severity in (Severity.HIGH, Severity.CRITICAL):
            return ExtractResponse(
                success=False,
                error=f"Document failed security scan: {scan_verdict.detail}"[:500],
            )

    try:
        result = await _run_engine(engine, data, body.filename)
    except Exception as exc:
        logger.warning("Extraction failed for %r: %s", body.filename, exc)
        return ExtractResponse(success=False, error=str(exc)[:500])

    return _build_extract_response(result)


def _build_extract_response(result) -> ExtractResponse:
    """Shape an engine's ``ExtractionResult`` into the wire
    ``ExtractResponse`` — shared by ``/extract`` and ``/extract-batch``."""
    pages = result.pages
    page_texts = [
        ExtractedPageText(
            page_number=page.page_number, text=page.text, markdown=page.markdown
        )
        for page in pages
        if page.text
    ]
    text = "\n\n".join(p.text for p in page_texts).strip()
    images = [
        ExtractedImage(
            data_base64=base64.b64encode(img.data).decode("ascii"),
            media_type=img.media_type,
            page_number=img.page_number,
            label=img.label,
            confidence=img.confidence,
            caption=img.caption,
            id=img.id,
        )
        for page in pages
        for img in page.images
    ]

    if not text and not images:
        return ExtractResponse(
            success=False,
            error="No extractable content found (empty or scanned document)",
            engine=result.engine,
        )

    return ExtractResponse(
        success=True,
        text=text,
        pages=page_texts,
        images=images,
        engine=result.engine,
        page_count=len(pages),
        markdown=result.markdown,
    )


async def _validate_batch_item(
    engine: ExtractionEngine, cfg, item: ExtractRequest
) -> tuple[bytes | None, str | None]:
    """Per-item validation + security scan for ``/extract-batch`` — soft
    failures only (``(None, error)``), never an ``HTTPException``: one
    malformed or blocked file in a batch of N shouldn't fail the other
    N-1. Contrast with ``/extract``, which stays strict (raises) for the
    single-file case above."""
    if not engine.accepts(item.filename, item.content_type):
        return None, (
            f"Unsupported content_type {item.content_type!r} for filename "
            f"{item.filename!r}. Supported by the running {engine.name!r} "
            f"engine: {sorted(engine.supported_formats())}"
        )
    try:
        data = base64.b64decode(item.content_base64, validate=True)
    except Exception as exc:
        return None, f"Invalid base64 content: {exc}"

    if len(data) > cfg.max_upload_bytes:
        return None, f"File exceeds maximum size of {cfg.max_upload_bytes} bytes"

    if getattr(cfg, "enable_document_security_scan", True):
        from substrate.kernel.agent.safety import Severity
        from substrate.runtimes.document_intelligence.security_scan import scan_document

        scan_verdict = await asyncio.to_thread(
            scan_document, data, filename=item.filename
        )
        if scan_verdict.flagged:
            logger.warning(
                "doc-firewall flagged %r (%s): %s",
                item.filename,
                scan_verdict.severity,
                scan_verdict.detail,
            )
        if scan_verdict.severity in (Severity.HIGH, Severity.CRITICAL):
            return None, f"Document failed security scan: {scan_verdict.detail}"[:500]

    return data, None


@router.post("/extract-batch", response_model=list[ExtractResponse])
async def extract_batch(body: ExtractBatchRequest, request: Request, _: Authed):
    """Extract multiple documents in one batched engine call where the
    engine supports it.

    Real, measured motivation (``PaddleClassicEngine``/``PaddleVLEngine``):
    a single document's pages often don't carry enough text regions to
    fill a large OCR batch on their own, leaving real GPU-batching
    headroom unused. This groups inference across every file in the batch
    instead of one sequential ``/extract`` call per file.
    """
    cfg = request.app.state.config
    engine: ExtractionEngine = request.app.state.engine

    responses: list[ExtractResponse | None] = [None] * len(body.items)
    validated: list[tuple[int, bytes, str]] = []
    for i, item in enumerate(body.items):
        data, error = await _validate_batch_item(engine, cfg, item)
        if error is not None:
            responses[i] = ExtractResponse(success=False, error=error)
        else:
            assert data is not None
            validated.append((i, data, item.filename))

    if validated:
        try:
            results = await _run_engine_batch(
                engine, [(data, filename) for _, data, filename in validated]
            )
        except Exception as exc:
            logger.warning(
                "Batch extraction failed for %d file(s): %s", len(validated), exc
            )
            for i, _, _ in validated:
                responses[i] = ExtractResponse(success=False, error=str(exc)[:500])
        else:
            for (i, _, _), result in zip(validated, results):
                responses[i] = _build_extract_response(result)

    return responses


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    cfg = request.app.state.config
    engine: ExtractionEngine = request.app.state.engine
    resolved = request.app.state.resolved
    return HealthResponse(
        status="ok",
        pod_name=cfg.pod_name,
        uptime_seconds=time.monotonic() - request.app.state.start_time,
        engine=engine.name,
        requested_mode=cfg.mode,
        resolved_mode=resolved.mode,
        degraded_from=resolved.degraded_from,
        worker_count=resolved.worker_count,
    )
