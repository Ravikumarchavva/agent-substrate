"""REST endpoints for the document-intelligence service.

All endpoints are prefixed with ``/v1/``.
Authentication is via ``Bearer <token>`` header (optional, configurable).
"""

from __future__ import annotations

import logging

import asyncio
import base64
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response

from substrate.documents import ExtractionResult

from . import dispatch
from .engines.base import ExtractionEngine
from .schemas import ExtractRequest, HealthResponse

logger = logging.getLogger(__name__)

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


def _json(result: ExtractionResult) -> Response:
    return Response(result.model_dump_json(), media_type="application/json")


@router.post("/extract")
async def extract(body: ExtractRequest, request: Request, _: Authed) -> Response:
    """Read a document: ``ExtractionResult`` as JSON. An answer is final — a document the scan blocks or nothing can read is a ``200``
    with ``success=false`` and the reason; only a malformed request (bad base64, too large) is an HTTP error."""
    cfg = request.app.state.config
    try:
        data = base64.b64decode(body.content_base64, validate=True)
    except Exception as exc:
        raise HTTPException(400, f"Invalid base64 content: {exc}") from exc

    if len(data) > cfg.max_upload_bytes:
        raise HTTPException(413, f"File exceeds maximum size of {cfg.max_upload_bytes} bytes")

    # Structural/security scan on the RAW bytes, before any parser touches them. Only HIGH/CRITICAL (doc-firewall's own BLOCK
    # verdict — definitive evidence) rejects; MEDIUM (review-worthy heuristics) is logged and the read goes on.
    if getattr(cfg, "enable_document_security_scan", True):
        from substrate.safety import Severity
        from document_intelligence.security_scan import scan_document

        verdict = await asyncio.to_thread(scan_document, data, filename=body.filename)
        if verdict.flagged:
            logger.warning("doc-firewall flagged %r (%s): %s", body.filename, verdict.severity, verdict.detail)
        if verdict.severity in (Severity.HIGH, Severity.CRITICAL):
            return _json(ExtractionResult(success=False, engine="security-scan", error=f"Document failed security scan: {verdict.detail}"[:500]))

    state = request.app.state
    result = await dispatch.read(state.native, state.engine, data, body.filename, body.content_type, body.strategy)
    return _json(result)


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
