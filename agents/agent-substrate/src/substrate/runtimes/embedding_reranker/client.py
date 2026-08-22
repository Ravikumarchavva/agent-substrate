"""HTTP client for the embedding-reranker service.

Used by LocalRagBackend for multimodal embedding + reranking, without
loading the llama-server sidecar plumbing into the main API process — see
embedding_reranker/service/ for the service itself. A separate service from
document_intelligence: shares no code or state with it, split out so one
person can own embedding/reranking infra without touching OCR/layout code.

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

_DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)


class EmbedResponse(BaseModel):
    embedding: list[float]


class RerankResponse(BaseModel):
    scores: list[float]


class HealthResponse(BaseModel):
    status: str
    pod_name: str
    uptime_seconds: float


class EmbeddingRerankerClient:
    """Async HTTP client for the embedding-reranker service."""

    def __init__(
        self,
        base_url: str = "http://embedding-reranker:8080",
        auth_token: str = "",
        timeout_s: float = 30.0,
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

    async def embed_image(self, data: bytes) -> list[float] | None:
        """Embed a chart/table image. Returns ``None`` on any failure —
        callers should skip indexing that one image, not fail the whole
        ingest."""
        import base64

        try:
            resp = await self._request(
                "POST",
                "/v1/embed",
                json={"image_base64": base64.b64encode(data).decode("ascii")},
            )
            return resp.json()["embedding"]
        except (httpx.HTTPError, KeyError) as exc:
            logger.warning("embed_image failed: %s", exc)
            return None

    async def embed_text(self, text: str) -> list[float] | None:
        """Embed a text query into the same space as ``embed_image`` (used
        to search the image collection). Returns ``None`` on any failure."""
        try:
            resp = await self._request("POST", "/v1/embed", json={"text": text})
            return resp.json()["embedding"]
        except (httpx.HTTPError, KeyError) as exc:
            logger.warning("embed_text failed: %s", exc)
            return None

    async def rerank(self, query: str, passages: list[str]) -> list[float] | None:
        """Score each passage's relevance to *query*, same order as input.
        Returns ``None`` on any failure — callers should fall back to the
        unreranked order, not fail the whole query."""
        if not passages:
            return []
        try:
            resp = await self._request(
                "POST", "/v1/rerank", json={"query": query, "passages": passages}
            )
            return resp.json()["scores"]
        except (httpx.HTTPError, KeyError) as exc:
            logger.warning("rerank failed: %s", exc)
            return None

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


__all__ = [
    "EmbeddingRerankerClient",
    "EmbedResponse",
    "RerankResponse",
    "HealthResponse",
]
