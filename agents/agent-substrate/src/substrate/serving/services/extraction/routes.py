"""REST endpoints for the document-extraction service.

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

from .schemas import (
    EmbedRequest,
    EmbedResponse,
    ExtractedImage,
    ExtractedPageText,
    ExtractRequest,
    ExtractResponse,
    HealthResponse,
    RerankRequest,
    RerankResponse,
)

logger = setup_logging()

router = APIRouter(prefix="/v1", tags=["extraction"])

# PaddleOCR/PaddleX reads PDF and raster images natively — no DOCX/PPTX
# parser (verified: no docx/pptx handling anywhere in paddlex's own readers).
# DOCX/PPTX chat attachments and RAG ingest fall back to metadata-only, same
# as when no extraction service is configured at all.
_SUPPORTED_CONTENT_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
}


async def _verify_token(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    """Validate Bearer token if EXTRACTION_AUTH_TOKEN is configured."""
    token = request.app.state.config.auth_token
    if not token:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")
    if authorization.removeprefix("Bearer ") != token:
        raise HTTPException(403, "Invalid token")


Authed = Annotated[None, Depends(_verify_token)]


@router.post("/extract", response_model=ExtractResponse)
async def extract(body: ExtractRequest, request: Request, _: Authed):
    """Extract layout-aware text + chart/table images from a document."""
    if body.content_type not in _SUPPORTED_CONTENT_TYPES:
        raise HTTPException(
            400,
            f"Unsupported content_type {body.content_type!r}. "
            f"Supported: {sorted(_SUPPORTED_CONTENT_TYPES)}",
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
    # them — a hostile file must not get a chance to exploit PaddleOCR/
    # PaddleX's own parsing first. See capabilities/safety/document_scanner.py
    # for what's actually verified working here (not just wired up).
    if getattr(cfg, "enable_document_security_scan", True):
        from substrate.capabilities.safety.document_scanner import scan_document

        scan_verdict = await asyncio.to_thread(
            scan_document, data, filename=body.filename
        )
        if scan_verdict.flagged:
            logger.warning(
                "doc-firewall flagged %r: %s", body.filename, scan_verdict.detail
            )
            return ExtractResponse(
                success=False,
                error=f"Document failed security scan: {scan_verdict.detail}"[:500],
            )

    pipeline = request.app.state.pipeline
    try:
        # PaddleOCR inference is CPU-bound and synchronous — run off the
        # event loop so one slow extraction doesn't stall every other
        # request this service is handling.
        pages = await asyncio.to_thread(pipeline.extract, data, body.filename)
    except Exception as exc:
        logger.warning("Extraction failed for %r: %s", body.filename, exc)
        return ExtractResponse(success=False, error=str(exc)[:500])

    page_texts = [
        ExtractedPageText(page_number=page.page_number, text=page.text)
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
        )
        for page in pages
        for img in page.images
    ]

    if not text and not images:
        return ExtractResponse(
            success=False,
            error="No extractable content found (empty or scanned document)",
        )

    return ExtractResponse(
        success=True,
        text=text,
        pages=page_texts,
        images=images,
        page_count=len(pages),
    )


@router.post("/embed", response_model=EmbedResponse)
async def embed(body: EmbedRequest, request: Request, _: Authed):
    """Embed either an image (``image_base64``) or text (``text``) into the
    shared multimodal space — exactly one must be set."""
    if bool(body.image_base64) == bool(body.text):
        raise HTTPException(
            400, "Exactly one of image_base64 or text must be provided."
        )

    embedding_reranker = request.app.state.embedding_reranker
    try:
        if body.image_base64:
            data = base64.b64decode(body.image_base64, validate=True)
            vector = await embedding_reranker.embed_image(data)
        else:
            assert body.text is not None
            vector = await embedding_reranker.embed_text(body.text)
    except Exception as exc:
        raise HTTPException(400, f"Embedding failed: {exc}") from exc

    return EmbedResponse(embedding=vector)


@router.post("/rerank", response_model=RerankResponse)
async def rerank(body: RerankRequest, request: Request, _: Authed):
    """Score each passage's relevance to ``query``, same order as input."""
    embedding_reranker = request.app.state.embedding_reranker
    try:
        scores = await embedding_reranker.rerank(body.query, body.passages)
    except Exception as exc:
        raise HTTPException(400, f"Rerank failed: {exc}") from exc

    return RerankResponse(scores=scores)


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    cfg = request.app.state.config
    return HealthResponse(
        status="ok",
        pod_name=cfg.pod_name,
        uptime_seconds=time.monotonic() - request.app.state.start_time,
    )
