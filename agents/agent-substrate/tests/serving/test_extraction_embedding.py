"""EmbeddingReranker — real SigLIP embedding + MiniLM reranking, no mocks.

Guarded by ``importorskip`` (sentence-transformers is the optional
`extraction` extra, not part of the default install)."""

from __future__ import annotations

import io

import pytest

pytest.importorskip("sentence_transformers")

from PIL import Image  # noqa: E402

from substrate.serving.services.extraction.embedding import EmbeddingReranker  # noqa: E402

_EMBEDDING_MODEL = "google/siglip-base-patch16-224"
_RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"


@pytest.fixture(scope="module")
def reranker() -> EmbeddingReranker:
    return EmbeddingReranker(
        embedding_model=_EMBEDDING_MODEL, reranker_model=_RERANKER_MODEL
    )


def _png_bytes(color: str) -> bytes:
    img = Image.new("RGB", (64, 64), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_embed_image_returns_768dim_vector(reranker: EmbeddingReranker):
    vector = reranker.embed_image(_png_bytes("red"))
    assert len(vector) == 768
    assert all(isinstance(v, float) for v in vector)


def test_embed_text_returns_768dim_vector(reranker: EmbeddingReranker):
    vector = reranker.embed_text("a bar chart showing quarterly revenue")
    assert len(vector) == 768


def test_rerank_scores_relevant_passage_higher(reranker: EmbeddingReranker):
    scores = reranker.rerank(
        "What was quarterly revenue?",
        [
            "The weather in San Francisco was sunny with a high of 65F.",
            "Quarterly revenue grew 20% year over year to $500M.",
        ],
    )
    assert len(scores) == 2
    assert scores[1] > scores[0]


def test_rerank_empty_passages_returns_empty_list(reranker: EmbeddingReranker):
    assert reranker.rerank("query", []) == []


def test_warmup_does_not_raise(reranker: EmbeddingReranker):
    reranker.warmup()
