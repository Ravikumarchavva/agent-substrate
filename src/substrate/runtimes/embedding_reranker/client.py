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

import math
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import httpx2 as httpx
from pydantic import BaseModel

from substrate.kernel.core.content import ContentBlock, MediaBlock, TextBlock
from substrate.kernel.exceptions import UnsupportedContentError

if TYPE_CHECKING:
    from substrate.kernel.llm import EmbeddingResult

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

    async def embed_blocks(self, blocks: Sequence[ContentBlock]) -> list[float] | None:
        """Embed mixed text + image content as a single vector when supported.

        The sidecar accepts text with multimodal payloads in one request, but some
        versions reject mixed prompts. When that happens, fall back to separately
        embedding each modality and returning the L2-normalized mean vector.
        """
        if not blocks:
            return []

        text_parts: list[str] = []
        images: list[bytes] = []
        for block in blocks:
            if isinstance(block, TextBlock):
                text_parts.append(block.text)
            elif isinstance(block, MediaBlock):
                if block.url is not None or block.file_id is not None:
                    raise UnsupportedContentError(
                        "EmbeddingRerankerClient requires resolved media bytes for multimodal blocks; callers must fetch url/file_id content first."
                    )
                if block.data is None:
                    raise UnsupportedContentError("MediaBlock received without payload bytes.")
                images.append(block.data)
            else:
                text_parts.append(str(block))

        text = "\n".join(text_parts).strip()
        if not text and not images:
            return []

        try:
            if images:
                payload: dict[str, Any] = {"text": text}
                if text:
                    payload["multimodal_data"] = [
                        __import__("base64").b64encode(image).decode("ascii") for image in images
                    ]
                else:
                    payload["multimodal_data"] = [
                        __import__("base64").b64encode(image).decode("ascii") for image in images
                    ]
                resp = await self._request("POST", "/v1/embed", json=payload)
                return resp.json()["embedding"]
            return await self.embed_text(text)
        except (httpx.HTTPError, KeyError, ValueError):
            if not images:
                return await self.embed_text(text)
            vecs = []
            if text:
                text_vec = await self.embed_text(text)
                if text_vec is not None:
                    vecs.append(text_vec)
            for image in images:
                img_vec = await self.embed_image(image)
                if img_vec is not None:
                    vecs.append(img_vec)
            if not vecs:
                return None
            mean = [0.0] * len(vecs[0])
            for vec in vecs:
                for i, value in enumerate(vec):
                    mean[i] += value
            for i in range(len(mean)):
                mean[i] /= len(vecs)
            norm = math.sqrt(sum(v * v for v in mean))
            if norm == 0:
                return mean
            return [v / norm for v in mean]

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


class EmbeddingRerankerTextEmbeddingClient:
    """Adapts ``EmbeddingRerankerClient.embed_text()`` to the kernel
    ``EmbeddingClient`` Protocol (``embed``/``embed_single`` —
    ``substrate.kernel.llm.EmbeddingClient``).

    A shape unification, not a dimension one: this service's embedding
    space (``RAG_IMAGE_EMBEDDING_DIM``, 2048-dim — ``PgVectorStore``
    already switches its column type from ``vector`` to ``halfvec`` above
    2000 dims to accommodate it) stays intentionally distinct from the
    main text-embedding model's space (1536-dim, or whatever
    ``EMBEDDING_MODEL`` resolves to). This only lets code written
    generically against ``EmbeddingClient`` call this service's text
    embedding without a bespoke ``embed_text``-shaped call.
    """

    def __init__(
        self, client: EmbeddingRerankerClient, *, model: str = "qwen3-vl-embedding-2b"
    ) -> None:
        self._client = client
        self._model = model

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        from substrate.kernel.llm import EmbeddingResult

        embeddings: list[list[float]] = []
        for text in texts:
            vec = await self._client.embed_text(text)
            if vec is None:
                raise RuntimeError(
                    f"embedding-reranker service failed to embed text: {text[:80]!r}"
                )
            embeddings.append(vec)
        return EmbeddingResult(embeddings=embeddings, model=self._model)

    async def embed_single(self, text: str) -> list[float]:
        vec = await self._client.embed_text(text)
        if vec is None:
            raise RuntimeError("embedding-reranker service failed to embed text")
        return vec

    async def embed_blocks(self, blocks: Sequence[ContentBlock]) -> list[float]:
        vec = await self._client.embed_blocks(blocks)
        if vec is None:
            raise RuntimeError("embedding-reranker service failed to embed mixed content")
        return vec


__all__ = [
    "EmbeddingRerankerClient",
    "EmbeddingRerankerTextEmbeddingClient",
    "EmbedResponse",
    "RerankResponse",
    "HealthResponse",
]
