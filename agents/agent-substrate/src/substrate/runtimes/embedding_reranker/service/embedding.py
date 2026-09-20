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
from typing import Sequence

import httpx2 as httpx

logger = logging.getLogger(__name__)


class EmbeddingServiceError(RuntimeError):
    """Raised when the llama-embed/llama-rerank sidecar is unreachable or
    returns a response in an unexpected shape."""


# Image-to-token ratio (~1024 px per patch token; see docs/capabilities/08-document-intelligence.md)
_PIXELS_PER_IMAGE_TOKEN = 1024

# Default pixel budget sized for 1024 token slot ceiling (~977 image tokens)
_DEFAULT_MAX_IMAGE_PIXELS = 1_000_000


def _downscale_to_pixel_budget(data: bytes, max_pixels: int) -> bytes:
    """Shrink an image to fit ``max_pixels``, preserving aspect ratio.

    Real, measured problem this solves: an 81-page sustainability report
    extracted 60 images, of which 13 were rejected outright with "request
    (1053-1641 tokens) exceeds the available context size (1024 tokens)"
    and silently dropped by the caller's degrade path. Every one was a
    large chart -- the most information-dense images in the document, and
    the ones most worth retrieving.

    Only the EMBEDDING input is reduced. Callers that also store the image
    keep their own original bytes, so nothing is downscaled on disk.
    Returns the input unchanged if it already fits or cannot be decoded --
    an unreadable image is the existing error path's problem, not this
    function's.
    """
    import io

    from PIL import Image

    try:
        img = Image.open(io.BytesIO(data))
        width, height = img.size
    except Exception:
        return data

    if width * height <= max_pixels or width < 1 or height < 1:
        return data

    scale = (max_pixels / (width * height)) ** 0.5
    new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
    try:
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        buffer = io.BytesIO()
        img.resize(new_size, Image.LANCZOS).save(buffer, format="PNG")
    except Exception:
        return data
    logger.debug(
        "Downscaled image %dx%d -> %dx%d for embedding (budget %d px)",
        width,
        height,
        new_size[0],
        new_size[1],
        max_pixels,
    )
    return buffer.getvalue()


class EmbeddingReranker:
    def __init__(
        self,
        *,
        embed_server_url: str,
        rerank_server_url: str,
        timeout: float = 30.0,
        max_image_pixels: int = _DEFAULT_MAX_IMAGE_PIXELS,
    ) -> None:
        self._embed_url = embed_server_url.rstrip("/")
        self._rerank_url = rerank_server_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)
        self._cached_media_marker: str | None = None
        self._max_image_pixels = max_image_pixels

    async def aclose(self) -> None:
        await self._client.aclose()

    async def warmup(self) -> None:
        try:
            await self.embed_text("warmup")
            await self.rerank("warmup query", ["warmup passage"])
        except EmbeddingServiceError as exc:
            logger.info("Embedding/rerank sidecar warmup skipped (%s)", exc)

    async def embed_image(self, data: bytes) -> list[float]:
        # Build llama-server multimodal prompt payload using dynamic media marker
        marker = await self._media_marker()
        fitted = _downscale_to_pixel_budget(data, self._max_image_pixels)
        b64 = base64.b64encode(fitted).decode("ascii")
        payload = {"input": {"prompt_string": marker, "multimodal_data": [b64]}}
        return await self._embed(payload)

    async def embed_mixed(self, parts: Sequence[str | bytes]) -> list[float]:
        """Embed one prompt of interleaved text and image parts as a single vector.

        Generalizes ``embed_image``'s verified mechanism (a ``prompt_string``
        with the dynamic ``media_marker`` in place of each image, plus
        ``multimodal_data`` carrying the actual image bytes) to also carry
        text: a ``str`` item in ``parts`` is literal prompt text, a ``bytes``
        item is image data inserted as the marker, in the order given.
        """
        marker = await self._media_marker()
        prompt_segments: list[str] = []
        images_b64: list[str] = []
        for part in parts:
            if isinstance(part, (bytes, bytearray)):
                fitted = _downscale_to_pixel_budget(bytes(part), self._max_image_pixels)
                images_b64.append(base64.b64encode(fitted).decode("ascii"))
                prompt_segments.append(marker)
            else:
                prompt_segments.append(str(part))
        prompt_string = " ".join(seg for seg in prompt_segments if seg)
        payload = {
            "input": {"prompt_string": prompt_string, "multimodal_data": images_b64}
        }
        return await self._embed(payload)

    async def embed_images(self, images: list[bytes]) -> list[list[float]]:
        """Embed many images in one round trip — same batching win as
        ``embed_texts`` (real, verified: 2 images in 0.08s in one request).
        If ANY image in the batch fails (e.g. exceeds the sidecar's image
        token minimum), the WHOLE request fails with no partial results
        (same behavior as the text batch endpoint) — callers doing
        heterogeneous batches should catch ``EmbeddingServiceError`` and
        fall back to per-item ``embed_image`` calls to isolate the bad one.
        """
        if not images:
            return []
        marker = await self._media_marker()
        payload = {
            "input": [
                {
                    "prompt_string": marker,
                    "multimodal_data": [
                        base64.b64encode(
                            _downscale_to_pixel_budget(data, self._max_image_pixels)
                        ).decode("ascii")
                    ],
                }
                for data in images
            ]
        }
        try:
            resp = await self._client.post(
                f"{self._embed_url}/embeddings", json=payload
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise EmbeddingServiceError(
                f"llama-embed sidecar batch request failed ({self._embed_url}): {exc}"
            ) from exc
        data = resp.json()
        try:
            rows = sorted(data, key=lambda row: row["index"])
            return [list(row["embedding"][0]) for row in rows]
        except (KeyError, IndexError, TypeError) as exc:
            raise EmbeddingServiceError(
                f"Unexpected /embeddings batch response shape: {data!r}"
            ) from exc

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

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed many texts in one round trip — real, verified ~50x fewer
        round trips than calling ``embed_text`` in a loop (3 texts: 0.04s
        batched vs full network RTT x3 sequential). If ANY single text in
        the batch exceeds the sidecar's context size, the WHOLE request
        400s with no partial results (verified against the real running
        sidecar) — callers doing large/heterogeneous batches should catch
        ``EmbeddingServiceError`` and fall back to per-item ``embed_text``
        calls to isolate just the offending one, same as
        DocumentIngestPipeline.ingest_file does.
        """
        if not texts:
            return []
        try:
            resp = await self._client.post(
                f"{self._embed_url}/embeddings", json={"input": texts}
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise EmbeddingServiceError(
                f"llama-embed sidecar batch request failed ({self._embed_url}): {exc}"
            ) from exc
        data = resp.json()
        try:
            # Same per-sequence wrapper as the single-input shape (see
            # _embed's own comment) — one row per input, sorted by `index`
            # since batch responses aren't guaranteed to preserve request
            # order (not verified either way; sorting is free and correct
            # regardless).
            rows = sorted(data, key=lambda row: row["index"])
            return [list(row["embedding"][0]) for row in rows]
        except (KeyError, IndexError, TypeError) as exc:
            raise EmbeddingServiceError(
                f"Unexpected /embeddings batch response shape: {data!r}"
            ) from exc

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
            # Unwrap pooled vector list [[float, ...]] -> [float, ...]
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
