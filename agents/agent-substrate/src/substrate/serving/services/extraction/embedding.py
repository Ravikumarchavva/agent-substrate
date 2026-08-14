"""Thin async HTTP client for the llama-embed / llama-rerank sidecars.

Both are real ``llama-server`` processes (see ``deployment/docker/docker-compose.yml``)
serving Qwen3-VL-Embedding-2B (``--embedding --pooling last``, ``POST
/embeddings``) and Qwen3-VL-Reranker-2B (``--reranking``, ``POST /rerank``)
respectively — replacing the previous in-process SentenceTransformer/
CrossEncoder models. Request/response shapes for text embedding and rerank
were verified via real ``curl`` calls against the running sidecars; see
``docs/claude_docs/decisions.md``. Image embedding wire format is NOT
verified — see the ``TODO`` on ``embed_image`` below.
"""

from __future__ import annotations

import base64
import logging

import httpx

logger = logging.getLogger(__name__)


class EmbeddingServiceError(RuntimeError):
    """Raised when the llama-embed/llama-rerank sidecar is unreachable or
    returns a response in an unexpected shape."""


class EmbeddingReranker:
    def __init__(
        self,
        *,
        embed_server_url: str,
        rerank_server_url: str,
        timeout: float = 30.0,
    ) -> None:
        self._embed_url = embed_server_url.rstrip("/")
        self._rerank_url = rerank_server_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def warmup(self) -> None:
        try:
            await self.embed_text("warmup")
            await self.rerank("warmup query", ["warmup passage"])
        except EmbeddingServiceError as exc:
            logger.info("Embedding/rerank sidecar warmup skipped (%s)", exc)

    async def embed_image(self, data: bytes) -> list[float]:
        # TODO: verify image embedding wire format against real llama-server
        # /embeddings call. This uses the OpenAI-compatible multimodal
        # content-part shape (data URL under `image_url.url`), which is
        # llama.cpp's convention for image input elsewhere (e.g.
        # /v1/chat/completions with mtmd), but this exact shape has NOT been
        # curl-verified against /embeddings the way the text path has.
        b64 = base64.b64encode(data).decode("ascii")
        payload = {
            "input": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                }
            ]
        }
        return await self._embed(payload)

    async def embed_text(self, text: str) -> list[float]:
        return await self._embed({"input": text})

    async def _embed(self, payload: dict) -> list[float]:
        try:
            resp = await self._client.post(
                f"{self._embed_url}/embeddings", json=payload
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise EmbeddingServiceError(
                f"llama-embed sidecar request failed ({self._embed_url}): {exc}"
            ) from exc
        data = resp.json()
        try:
            return list(data[0]["embedding"])
        except (KeyError, IndexError, TypeError) as exc:
            raise EmbeddingServiceError(
                f"Unexpected /embeddings response shape: {data!r}"
            ) from exc

    async def rerank(
        self,
        query: str,
        passages: list[str],
        *,
        image: bytes | None = None,
    ) -> list[float]:
        """Returns one relevance score per passage, same order as input.

        ``image`` is accepted for a future visual-candidate rerank path
        (query + image + caption scored together) but llama-server's
        ``/rerank`` endpoint support for an image field is NOT confirmed —
        if set, this currently falls back to text-only reranking and logs a
        warning rather than silently dropping the image.
        """
        if not passages:
            return []
        if image is not None:
            logger.warning(
                "rerank() image-aware scoring is not confirmed supported by "
                "llama-server /rerank; falling back to text-only reranking"
            )
        try:
            resp = await self._client.post(
                f"{self._rerank_url}/rerank",
                json={"query": query, "documents": passages},
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise EmbeddingServiceError(
                f"llama-rerank sidecar request failed ({self._rerank_url}): {exc}"
            ) from exc
        data = resp.json()
        try:
            results = data["results"]
        except (KeyError, TypeError) as exc:
            raise EmbeddingServiceError(
                f"Unexpected /rerank response shape: {data!r}"
            ) from exc

        scores = [0.0] * len(passages)
        try:
            for result in results:
                index = result["index"]
                if 0 <= index < len(scores):
                    scores[index] = float(result["relevance_score"])
        except (KeyError, TypeError) as exc:
            raise EmbeddingServiceError(
                f"Unexpected /rerank result entry shape: {results!r}"
            ) from exc
        return scores


__all__ = ["EmbeddingReranker", "EmbeddingServiceError"]
