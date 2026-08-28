"""ask() — kind-based text/image classification and blob-store rehydration,
exercised against fakes (real retrieval/generation is covered elsewhere;
this targets the two behaviors this session's storage rewrite changed)."""

from __future__ import annotations

from substrate.capabilities.knowledge.ask import _is_text, ask
from substrate.kernel.core.content import ImageBlock, TextBlock
from substrate.kernel.core.usage import Usage
from substrate.kernel.llm.llm import LLMResponse
from substrate.kernel.storage.vector import SearchResult


def _text_result(**metadata) -> SearchResult:
    return SearchResult(
        id="t1", content=[TextBlock(text="hello")], score=0.9, metadata=metadata
    )


def _image_result(**metadata) -> SearchResult:
    return SearchResult(
        id="i1",
        content=[ImageBlock(data=b"png-bytes", media_type="image/png")],
        score=0.8,
        metadata=metadata,
    )


def _caption_result(**metadata) -> SearchResult:
    """The shape a stored-not-inlined image row actually has: a TextBlock
    caption, not an ImageBlock."""
    return SearchResult(
        id="i2", content=[TextBlock(text="a chart")], score=0.8, metadata=metadata
    )


# ── _is_text ──────────────────────────────────────────────────────────────


def test_is_text_uses_kind_metadata_when_present():
    assert _is_text(_caption_result(kind="image")) is False
    assert _is_text(_text_result(kind="text")) is True


def test_is_text_falls_back_to_block_type_without_kind_metadata():
    """Pre-existing rows (ingested before `kind` existed) or a different
    producer must still classify correctly by block type."""
    assert _is_text(_text_result()) is True
    assert _is_text(_image_result()) is False


def test_is_text_caption_row_without_kind_would_misclassify():
    """Documents the real bug this fixes: a caption TextBlock with no
    `kind` metadata looks like text by block type alone. Ingest always sets
    `kind`, so this is the fallback path's known limitation, not a live bug."""
    assert _is_text(_caption_result()) is True


# ── ask() fakes ───────────────────────────────────────────────────────────


class _FakeStore:
    def __init__(self, results: list[SearchResult]) -> None:
        self._results = results

    async def search(self, query_embedding, *, collection, limit, filter=None):
        return self._results


class _FakeEmbedder:
    async def embed_text(self, text: str) -> list[float]:
        return [0.1, 0.2]

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        return [1.0 for _ in documents]


class _FakeLLMClient:
    model = "fake"

    async def generate(self, messages, *, options=None):
        return LLMResponse(content=[TextBlock(text="the answer")], usage=Usage())


class _FakeBlobStore:
    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self._objects = objects or {}

    async def download(self, key: str) -> bytes:
        return self._objects[key]


async def _ask(results: list[SearchResult], **kwargs) -> "AskResult":  # noqa: F821
    return await ask(
        "what is in the chart?",
        store=_FakeStore(results),
        embedder=_FakeEmbedder(),
        llm_client=_FakeLLMClient(),
        collection="kb",
        use_kb_filter=False,
        rerank=False,
        **kwargs,
    )


async def test_citation_carries_image_key_and_pdf_key_from_metadata():
    result = _caption_result(
        kind="image", image_key="kb/a.pdf/images/img-p1-0.png", pdf_key="kb/a.pdf"
    )
    res = await _ask([result])

    assert res.citations[0].kind == "image"
    assert res.citations[0].image_key == "kb/a.pdf/images/img-p1-0.png"
    assert res.citations[0].pdf_key == "kb/a.pdf"
    assert res.citations[0].image_data is None  # no blob_store given


async def test_no_blob_store_means_no_rehydration_attempted():
    result = _caption_result(kind="image", image_key="missing-key")
    # No blob store configured at all -- must not raise or attempt a lookup.
    res = await _ask([result])
    assert res.citations[0].image_data is None


async def test_blob_store_rehydrates_image_data():
    key = "kb/a.pdf/images/img-p1-0.png"
    result = _caption_result(kind="image", image_key=key)
    blob_store = _FakeBlobStore({key: b"real-image-bytes"})

    res = await _ask([result], blob_store=blob_store)

    assert res.citations[0].image_data == b"real-image-bytes"


async def test_blob_store_download_failure_degrades_gracefully():
    """A missing/broken object must not fail the whole answer -- the
    citation just keeps its caption/snippet, same as backends/local.py's
    _rehydrate_image degrade-not-fail behavior."""
    result = _caption_result(kind="image", image_key="does-not-exist")
    blob_store = _FakeBlobStore({})  # empty -- download() will KeyError

    res = await _ask([result], blob_store=blob_store)

    assert res.citations[0].image_data is None
    assert res.citations[0].snippet == "a chart"


async def test_text_citation_is_never_rehydrated():
    result = _text_result(kind="text")
    blob_store = _FakeBlobStore({})

    res = await _ask([result], blob_store=blob_store)

    assert res.citations[0].image_key is None
    assert res.citations[0].image_data is None
