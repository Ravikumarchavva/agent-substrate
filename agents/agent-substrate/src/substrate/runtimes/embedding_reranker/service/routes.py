"""REST endpoints for the embedding-reranker service.

All endpoints are prefixed with ``/v1/``.
Authentication is via ``Bearer <token>`` header (optional, configurable).
"""

from __future__ import annotations
from substrate.logger import setup_logging

import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from .schemas import (
    EmbedRequest,
    EmbedResponse,
    HealthResponse,
    RerankRequest,
    RerankResponse,
)

logger = setup_logging()

router = APIRouter(prefix="/v1", tags=["embedding-reranker"])


async def _verify_token(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    """Validate Bearer token if EMBEDDING_RERANKER_AUTH_TOKEN is configured."""
    token = request.app.state.config.auth_token
    if not token:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")
    if authorization.removeprefix("Bearer ") != token:
        raise HTTPException(403, "Invalid token")


Authed = Annotated[None, Depends(_verify_token)]


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
            import base64

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
