"""REST endpoints for the Docling extraction service.

All endpoints are prefixed with ``/v1/``.
Authentication is via ``Bearer <token>`` header (optional, configurable).
"""

from __future__ import annotations
from substrate.logger import setup_logging

import base64
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from substrate.kernel.core.content import TextBlock

from .schemas import DoclingExtractResponse, ExtractRequest, HealthResponse

logger = setup_logging()

router = APIRouter(prefix="/v1", tags=["docling"])

_SUPPORTED_CONTENT_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",  # .pptx
    "text/html",
    "text/markdown",
}


async def _verify_token(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    """Validate Bearer token if DOCLING_AUTH_TOKEN is configured."""
    token = request.app.state.config.auth_token
    if not token:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")
    if authorization.removeprefix("Bearer ") != token:
        raise HTTPException(403, "Invalid token")


Authed = Annotated[None, Depends(_verify_token)]


@router.post("/extract", response_model=DoclingExtractResponse)
async def extract(body: ExtractRequest, request: Request, _: Authed):
    """Extract structure-aware text from a document."""
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

    loader = request.app.state.loader
    try:
        docs = await loader.load(data, metadata={"source": body.filename})
    except Exception as exc:
        logger.warning("Docling extraction failed for %r: %s", body.filename, exc)
        return DoclingExtractResponse(success=False, error=str(exc)[:500])

    text = "\n\n".join(
        block.text
        for doc in docs
        for block in doc.content
        if isinstance(block, TextBlock)
    ).strip()

    if not text:
        return DoclingExtractResponse(
            success=False, error="No extractable text found (empty or scanned document)"
        )

    return DoclingExtractResponse(success=True, text=text, page_count=len(docs))


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    cfg = request.app.state.config
    return HealthResponse(
        status="ok",
        pod_name=cfg.pod_name,
        uptime_seconds=time.monotonic() - request.app.state.start_time,
    )
