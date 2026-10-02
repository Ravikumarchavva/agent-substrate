"""REST endpoints for the embedding-reranker service.

All endpoints are prefixed with ``/v1/``.
Authentication is via ``Bearer <token>`` header (optional, configurable).
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from .llama import EngineError
from .schemas import (
    EmbeddingRow,
    EmbeddingsRequest,
    EmbeddingsResponse,
    HealthResponse,
    ModelInfo,
    ModelsResponse,
    RerankRequest,
    RerankResponse,
    RerankRow,
)

logger = logging.getLogger(__name__)

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


def _part(value: object) -> str | bytes:
    """One part of a mixed item: text as is, ``{"image": "<base64>"}`` as bytes."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("image"), str):
        try:
            return base64.b64decode(value["image"], validate=True)
        except Exception as exc:
            raise HTTPException(400, f"Invalid base64 image: {exc}") from exc
    raise HTTPException(400, "an input part is a string or {'image': '<base64>'}")


def _item(value: object) -> str | list[str | bytes]:
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("content"), list):
        return [_part(part) for part in value["content"]]
    if isinstance(value, dict) and "image" in value:
        return [_part(value)]
    raise HTTPException(400, "an input is a string, {'image': '<base64>'} or {'content': [...]}")


def _engine_failure(exc: Exception) -> HTTPException:
    """A sidecar that cannot be reached or answers nonsense is 503 (the caller may retry or fall back); too long an input is 400."""
    text = str(exc)
    if "exceeds the available context" in text or "too long" in text.lower():
        return HTTPException(400, f"input exceeds the maximum context length: {text[:300]}")
    return HTTPException(503, text[:300])


@router.post("/embeddings", response_model=EmbeddingsResponse)
async def embeddings(body: EmbeddingsRequest, request: Request, _: Authed):
    """Embed each input into the shared multimodal space — one vector per input, in order."""
    cfg = request.app.state.config
    items = [_item(value) for value in body.input]
    try:
        vectors = await request.app.state.engine.embed_batch(
            items, query=body.input_type == "query", instruction=getattr(cfg, "query_instruction", "")
        )
    except EngineError as exc:
        raise _engine_failure(exc) from exc
    return EmbeddingsResponse(
        data=[EmbeddingRow(index=i, embedding=vector) for i, vector in enumerate(vectors)],
        model=getattr(cfg, "embed_model_name", "qwen3-vl-embedding"),
    )


@router.post("/rerank", response_model=RerankResponse)
async def rerank(body: RerankRequest, request: Request, _: Authed):
    """Score each document's relevance to ``query``: ``results`` has a row per document (best first, at most ``top_n``)."""
    cfg = request.app.state.config
    try:
        scores = await request.app.state.engine.rerank(body.query, body.documents)
    except EngineError as exc:
        raise _engine_failure(exc) from exc
    rows = sorted((RerankRow(index=i, relevance_score=s) for i, s in enumerate(scores)), key=lambda r: -r.relevance_score)
    return RerankResponse(results=rows[: body.top_n] if body.top_n else rows, model=getattr(cfg, "rerank_model_name", "qwen3-vl-reranker"))


@router.get("/models", response_model=ModelsResponse)
async def models(request: Request, _: Authed) -> ModelsResponse:
    """What the embedding model is: its width, longest input and what it can embed — a client reads this once."""
    cfg = request.app.state.config
    return ModelsResponse(
        data=[
            ModelInfo(
                id=getattr(cfg, "embed_model_name", "qwen3-vl-embedding"),
                dimensions=getattr(cfg, "embedding_dim", 2048),
                max_input_tokens=getattr(cfg, "local_ctx_size", 2048),
                modalities=["text", "image"],
            )
        ]
    )


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    cfg = request.app.state.config
    return HealthResponse(
        status="ok",
        pod_name=cfg.pod_name,
        uptime_seconds=time.monotonic() - request.app.state.start_time,
    )
