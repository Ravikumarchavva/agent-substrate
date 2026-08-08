"""SigLIP multimodal embedding + MiniLM cross-encoder reranking.

Both verified live via ``sentence-transformers`` (5.7.0): SigLIP-base
embeds image and text into the same 768-dim space via the standard
``.encode()`` API with zero integration friction; the MiniLM cross-encoder
reranks via ``CrossEncoder.predict()``. (Jina's equivalents were also
benchmarked and rejected — they require pinning a yanked ``transformers``
version and, for the embedder, bypassing the standard ``.encode()`` API for
custom ``encode_image()``/``encode_text()`` calls. Real, measured cost for
no measured benefit here.)
"""

from __future__ import annotations

import io

from PIL import Image


class EmbeddingReranker:
    def __init__(self, *, embedding_model: str, reranker_model: str) -> None:
        from sentence_transformers import CrossEncoder, SentenceTransformer

        self._embedder = SentenceTransformer(embedding_model)
        self._reranker = CrossEncoder(reranker_model)

    def warmup(self) -> None:
        blank = Image.new("RGB", (32, 32), color="white")
        self._embedder.encode([blank])
        self._embedder.encode(["warmup"])
        self._reranker.predict([("warmup query", "warmup passage")])

    def embed_image(self, data: bytes) -> list[float]:
        img = Image.open(io.BytesIO(data)).convert("RGB")
        return self._embedder.encode([img])[0].tolist()

    def embed_text(self, text: str) -> list[float]:
        return self._embedder.encode([text])[0].tolist()

    def rerank(self, query: str, passages: list[str]) -> list[float]:
        """Returns one relevance score per passage, same order as input."""
        if not passages:
            return []
        pairs = [(query, p) for p in passages]
        scores = self._reranker.predict(pairs)
        return [float(s) for s in scores]


__all__ = ["EmbeddingReranker"]
