"""`/v1/embeddings`, `/v1/rerank`, `/v1/models`, `/v1/health`: the wires model clients already speak."""

from __future__ import annotations

import base64
import secrets
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header

from loamz.data.server.features.inference.model import (
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
from loamz.data.server.features.inference.service import InferenceService, inference
from loamz.data.server.kernel.http import use
from loamz.data.storage.errors import (
    ForbiddenError,
    InvalidRequestError,
    UnauthenticatedError,
)

router = APIRouter(prefix="/v1", tags=["inference"])
Service = Annotated[InferenceService, Depends(use(inference))]


def _authorized(service: Service, authorization: str | None = Header(default=None)) -> None:
    """The configured bearer token, when there is one."""
    token = service.settings.token.get_secret_value()
    if not token:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise UnauthenticatedError("Missing or invalid Authorization header")
    if not secrets.compare_digest(authorization.removeprefix("Bearer "), token):
        raise ForbiddenError("Invalid token")


Authorized = Annotated[None, Depends(_authorized)]


def _part(value: object) -> str | bytes:
    """One part of a mixed item: text as is, `{"image": "<base64>"}` as bytes."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("image"), str):
        try:
            return base64.b64decode(value["image"], validate=True)
        except ValueError as exc:
            raise InvalidRequestError(f"Invalid base64 image: {exc}") from exc
    raise InvalidRequestError("an input part is a string or {'image': '<base64>'}")


def _item(value: object) -> str | list[str | bytes]:
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("content"), list):
        return [_part(part) for part in value["content"]]
    if isinstance(value, dict) and "image" in value:
        return [_part(value)]
    raise InvalidRequestError("an input is a string, {'image': '<base64>'} or {'content': [...]}")


@router.post("/embeddings", response_model=EmbeddingsResponse)
async def embeddings(body: EmbeddingsRequest, service: Service, _: Authorized):
    """Embed each input into the shared multimodal space: one vector per input, in order."""
    vectors = await service.embed(
        [_item(value) for value in body.input], query=body.input_type == "query"
    )
    return EmbeddingsResponse(
        data=[EmbeddingRow(index=i, embedding=vector) for i, vector in enumerate(vectors)],
        model=service.settings.embed_model_name,
    )


@router.post("/rerank", response_model=RerankResponse)
async def rerank(body: RerankRequest, service: Service, _: Authorized):
    """Each document's relevance to `query`: a row per document, best first (at most `top_n`)."""
    scores = await service.rerank(body.query, body.documents)
    rows = sorted(
        (RerankRow(index=i, relevance_score=s) for i, s in enumerate(scores)),
        key=lambda r: -r.relevance_score,
    )
    return RerankResponse(
        results=rows[: body.top_n] if body.top_n else rows,
        model=service.settings.rerank_model_name,
    )


@router.get("/models", response_model=ModelsResponse)
async def models(service: Service, _: Authorized) -> ModelsResponse:
    """What the embedding model is: its width, longest input and what it embeds (a client reads this once)."""
    cfg = service.settings
    return ModelsResponse(
        data=[
            ModelInfo(
                id=cfg.embed_model_name,
                dimensions=cfg.embedding_dim,
                max_input_tokens=cfg.local_ctx_size,
                modalities=["text", "image"],
            )
        ]
    )


@router.get("/health", response_model=HealthResponse)
async def health(service: Service) -> HealthResponse:
    return HealthResponse(
        status="ok",
        pod_name=service.settings.pod_name,
        uptime_seconds=time.monotonic() - service.started_at,
    )
