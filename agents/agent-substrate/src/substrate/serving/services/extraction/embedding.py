"""Thin async HTTP client for the llama-embed / llama-rerank sidecars.

Both are real ``llama-server`` processes (see ``deployment/docker/docker-compose.yml``)
serving Qwen3-VL-Embedding-2B (``--embedding --pooling last``, ``POST
/embeddings``) and Qwen3-VL-Reranker-2B (``--reranking``, ``POST /rerank``)
respectively — replacing the previous in-process SentenceTransformer/
CrossEncoder models. Request/response shapes for text embedding, image
embedding, and rerank were all verified via real calls against the running
sidecars (not assumed from docs) — see ``docs/claude_docs/decisions.md``.
Two real bugs were caught this way and fixed: ``/embeddings``' response
nests one level deeper than expected (``embedding`` is a list of
per-sequence vectors, not a flat vector), and the OpenAI-compatible
chat-completions image_url shape does not work against this server's
``/embeddings`` at all — the real accepted shape is
``{"prompt_string": ..., "multimodal_data": [base64, ...]}``, found by
reading llama.cpp's own source.
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
        self._cached_media_marker: str | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def warmup(self) -> None:
        try:
            await self.embed_text("warmup")
            await self.rerank("warmup query", ["warmup passage"])
        except EmbeddingServiceError as exc:
            logger.info("Embedding/rerank sidecar warmup skipped (%s)", exc)

    async def embed_image(self, data: bytes) -> list[float]:
        # Verified against the real running llama-embed sidecar (not the
        # OpenAI-compatible chat-completions image_url shape, which this
        # server's /embeddings rejects outright with a 500). The real
        # accepted shape, confirmed by reading llama.cpp's own
        # tokenize_input_subprompt() source: `input` (== "prompt") as
        # `{"prompt_string": ..., "multimodal_data": [base64, ...]}`, where
        # prompt_string must contain the server's media-placeholder marker
        # (`get_media_marker()` in server-common.cpp) at the position the
        # image tokens should be inserted — the marker is randomized per
        # server instance unless `LLAMA_MEDIA_MARKER` is pinned, so it's
        # fetched from `/props` rather than hardcoded.
        marker = await self._media_marker()
        b64 = base64.b64encode(data).decode("ascii")
        payload = {"input": {"prompt_string": marker, "multimodal_data": [b64]}}
        return await self._embed(payload)

    async def _media_marker(self) -> str:
        marker = self._cached_media_marker
        if marker is None:
            try:
                resp = await self._client.get(f"{self._embed_url}/props")
                resp.raise_for_status()
                marker = str(resp.json()["media_marker"])
            except (httpx.HTTPError, KeyError) as exc:
                raise EmbeddingServiceError(
                    f"Could not fetch media_marker from llama-embed /props "
                    f"({self._embed_url}): {exc}"
                ) from exc
            self._cached_media_marker = marker
        return marker

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
            # `embedding` is itself a list of per-sequence vectors (llama-server's
            # OAI-compatible /embeddings shape) — one row per pooled sequence, not
            # a flat vector directly. With --pooling last there is exactly one row
            # per input, so [0] is the real vector. Confirmed against the real
            # running sidecar (not assumed from docs): `data[0]["embedding"]` is
            # `[[float, ...]]`, a single-element wrapper around the actual 2048
            # floats — an earlier version of this method returned that wrapper
            # unflattened, which would have hard-failed Postgres's vector(2048)
            # cast on first real write.
            return list(data[0]["embedding"][0])
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
