"""``Library`` with an embedder and a reranker: chunks, vectors, hybrid and reranked ``find``, and a service that goes down and comes back."""

from __future__ import annotations

import hashlib
import re
from types import SimpleNamespace

import pytest

from substrate.documents import DocumentsTool, Library
from substrate.documents.types import ExtractedImage, ExtractedImageLabel, ExtractedPage, ExtractionResult
from substrate.models import Modality
from substrate.models.protocols import EmbeddingResult
from substrate.types import MediaBlock, TextBlock
from substrate.types.errors import ContextLengthError, ServiceUnavailableError, VectorSpaceError
from substrate.types.run import RunScope

C = "tenants/acme/knowledge/handbook"
WIDTH = 64


def _vector(text: str) -> list[float]:
    """A bag-of-words vector: texts sharing words are close, which is all the tests need from 'meaning'."""
    vec = [0.0] * WIDTH
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        vec[int(hashlib.sha1(word.encode()).hexdigest(), 16) % WIDTH] += 1.0
    return vec


class FakeEmbedder:
    def __init__(self, *, model: str = "fake-embed", images: bool = False, max_input_tokens: int = 512) -> None:
        self.model, self.dimensions, self.max_input_tokens = model, None, max_input_tokens
        self.modalities = frozenset({Modality.TEXT, Modality.IMAGE}) if images else frozenset({Modality.TEXT})
        self.down = False
        self.too_long_over: int | None = None
        self.calls: list[tuple[int, bool]] = []

    async def embed(self, inputs, *, query: bool = False):
        self.calls.append((len(inputs), query))
        if self.down:
            raise ServiceUnavailableError("embedder is down")
        texts = []
        for item in inputs:
            text = item if isinstance(item, str) else " ".join(b.text if isinstance(b, TextBlock) else "FIGURE" for b in item)
            if self.too_long_over is not None and len(text) > self.too_long_over:
                raise ContextLengthError("input exceeds the maximum context length")
            texts.append(text)
        self.dimensions = WIDTH
        return EmbeddingResult(embeddings=[_vector(t) for t in texts], model=self.model)


class FakeReranker:
    model = "fake-rerank"

    def __init__(self) -> None:
        self.down = False
        self.seen: list[list[str]] = []

    async def rerank(self, query, passages):
        if self.down:
            raise ServiceUnavailableError("reranker is down")
        self.seen.append(list(passages))
        words = set(re.findall(r"[a-z]+", query.lower()))
        return [float(len(words & set(re.findall(r"[a-z]+", p.lower())))) for p in passages]


HANDBOOK = (
    "<!-- page 1 -->\n\n# Employee Handbook\n\n## Refunds\n\nCustomers may return damaged goods within thirty days for a full refund. "
    + "Refund requests are handled by the support desk. " * 30
    + "\n\n<!-- page 2 -->\n\n## Shipping\n\nOrders to Rotterdam ship from the Dutch warehouse in two days. "
    + "Carriers collect parcels every afternoon. " * 30
)


def handbook() -> ExtractionResult:
    return ExtractionResult(pages=[ExtractedPage(page_number=1, text="x"), ExtractedPage(page_number=2, text="y")], markdown=HANDBOOK, engine="test")


async def test_a_collection_with_an_embedder_is_searched_by_meaning_and_words_then_reranked(store):
    embedder, reranker = FakeEmbedder(), FakeReranker()
    library = Library(store, embedder=embedder, reranker=reranker)
    added = await library.add(handbook(), "handbook.pdf", collection=C, sha256="ab" * 32)
    assert not added.warnings and added.sections == 2
    stored = await store.vectors.get([f"{added.document}:1:0"], collection=C)
    assert stored and stored[0].embedding and len(stored[0].embedding) == WIDTH and stored[0].metadata["kind"] == "text"
    assert await store.vectors.space_of(C) == ("fake-embed", WIDTH)

    hits = await library.find(collection=C, query="how do I return damaged goods for a refund")
    assert hits and hits[0].section.position == 1 and hits[0].document.document == added.document and hits.note is None  # section 1 opens with the title and holds Refunds
    assert (embedder.calls[-1][1] is True) and reranker.seen, "the query is embedded as a query, and the candidates are reranked"
    shipping = await library.find(collection=C, query="Rotterdam parcels", document=added.document)
    assert shipping[0].section.title == "Shipping"
    assert len({(h.document.document, h.section.position) for h in hits}) == len(hits), "chunks are folded into their sections"


async def test_chunks_are_cut_to_the_embedders_input_and_carry_their_heading_and_pages(store):
    embedder = FakeEmbedder(max_input_tokens=128)
    library = Library(store, embedder=embedder)
    added = await library.add(handbook(), "handbook.pdf", collection=C)
    chunks = [d for d in await store.vectors.get([f"{added.document}:{n}:{i}" for n in (1, 2) for i in range(40)], collection=C)]
    assert len(chunks) > 4 and all(len(d.to_text()) <= 64 * 3 * 2 for d in chunks), "chunks fit a 128-token model with room for the heading"
    shipping = next(d for d in chunks if d.metadata["heading"] == "Shipping")
    assert shipping.metadata["pages"] == [2, 2] and shipping.metadata["heading_path"][-1] == "Shipping" and shipping.metadata["title"]


async def test_without_an_embedder_find_is_words_only_and_no_vectors_are_stored(store):
    library = Library(store)
    added = await library.add(handbook(), "handbook.pdf", collection=C)
    assert (await library.find(collection=C, query="Rotterdam"))[0].section.title == "Shipping"
    assert await store.vectors.get([f"{added.document}:1:0"], collection=C) == []


async def test_a_service_that_is_down_never_fails_an_add_and_find_degrades_to_words_with_a_note(store):
    embedder = FakeEmbedder()
    embedder.down = True
    library = Library(store, embedder=embedder, reranker=FakeReranker())
    added = await library.add(handbook(), "handbook.pdf", collection=C)
    assert any("could not be embedded" in w for w in added.warnings) and added.sections == 2
    assert all(d.embedding is None for d in await store.vectors.unembedded(collection=C))
    hits = await library.find(collection=C, query="Rotterdam parcels")
    assert hits and hits[0].section.title == "Shipping" and "unavailable" in hits.note and "words only" in hits.note

    embedder.down = False  # the service comes back: embed what was stored without a vector
    assert await library.reindex(collection=C, missing_only=True) == 0
    assert await store.vectors.unembedded(collection=C) == [] and await store.vectors.space_of(C) == ("fake-embed", WIDTH)
    later = await library.find(collection=C, query="Rotterdam parcels")
    assert later and later[0].section.title == "Shipping" and later.note is None


async def test_a_service_that_stays_down_leaves_the_missing_chunks_counted(store):
    embedder = FakeEmbedder()
    embedder.down = True
    library = Library(store, embedder=embedder)
    await library.add(handbook(), "handbook.pdf", collection=C)
    left = await library.reindex(collection=C, missing_only=True)
    assert left == len(await store.vectors.unembedded(collection=C)) and left > 0


async def test_one_overlong_chunk_costs_one_vector_not_the_batch(store):
    embedder = FakeEmbedder()
    library = Library(store, embedder=embedder, chunk_tokens=100)
    embedder.too_long_over = 200  # the short passages fit, the long run of words cut at ~300 characters does not
    result = ExtractionResult(
        pages=[ExtractedPage(page_number=1, text="x")],
        markdown="<!-- page 1 -->\n\n## One\n\nshort passage about apples.\n\n" + ("long " * 150) + "\n\nanother short passage about pears.",
        engine="t",
    )
    added = await library.add(result, "mixed.md", collection=C)
    missing = await store.vectors.unembedded(collection=C)
    assert len(missing) >= 1 and added.sections == 1
    embedded = [d for d in await store.vectors.get([f"{added.document}:1:{i}" for i in range(10)], collection=C) if d.embedding]
    assert embedded, "the other chunks of the batch kept their vectors"


async def test_the_reranker_being_down_keeps_the_fused_order_with_a_note(store):
    reranker = FakeReranker()
    library = Library(store, embedder=FakeEmbedder(), reranker=reranker)
    await library.add(handbook(), "handbook.pdf", collection=C)
    reranker.down = True
    hits = await library.find(collection=C, query="refund damaged goods")
    assert hits and hits[0].section.position == 1 and "reranking service is unavailable" in hits.note


async def test_a_collection_written_by_one_embedder_refuses_another(store):
    await Library(store, embedder=FakeEmbedder(model="model-a")).add(handbook(), "a.pdf", collection=C, sha256="aa" * 32)
    other = Library(store, embedder=FakeEmbedder(model="model-b"))
    with pytest.raises(VectorSpaceError, match="model-a"):
        await other.add(handbook(), "b.pdf", collection=C, sha256="bb" * 32)
    with pytest.raises(VectorSpaceError):
        await other.find(collection=C, query="refund")
    tool = DocumentsTool(other, collection=lambda s: C)
    refused = await tool.execute(ctx=SimpleNamespace(scope=RunScope(tenant_id="acme")), action="find", query="refund")
    assert refused.is_error and "cannot be searched by meaning" in refused.text


async def test_figures_are_embedded_with_their_captions_in_the_same_space_when_the_model_takes_images(store):
    png = b"\x89PNG-fake"
    page = ExtractedPage(
        page_number=1,
        text="Revenue",
        images=[ExtractedImage(data=png, label=ExtractedImageLabel.CHART, caption="quarterly revenue by region chart", id="img-p1-0", page_number=1)],
    )
    result = ExtractionResult(pages=[page], markdown="<!-- page 1 -->\n\n## Results\n\nRevenue grew. " + "More text. " * 60 + "\n\n![chart](cid:img-p1-0)", engine="t")
    library = Library(store, embedder=FakeEmbedder(images=True))
    added = await library.add(result, "report.pdf", collection=C)
    figure = await store.vectors.get([f"{added.document}:img:img-p1-0"], collection=C)
    assert figure and figure[0].embedding and figure[0].metadata["kind"] == "image" and figure[0].to_text() == "quarterly revenue by region chart"
    hits = await library.find(collection=C, query="quarterly revenue by region chart")
    assert any(h.image == "img-p1-0" for h in hits)
    tool = DocumentsTool(library, collection=lambda s: C)
    found = await tool.execute(ctx=SimpleNamespace(scope=RunScope(tenant_id="acme")), action="find", query="quarterly revenue by region chart")
    assert "figure img-p1-0" in found.text

    other = "tenants/acme/knowledge/text-only"
    text_only = Library(store, embedder=FakeEmbedder(images=False))
    added2 = await text_only.add(result, "report.pdf", collection=other)
    assert await store.vectors.get([f"{added2.document}:img:img-p1-0"], collection=other) == []


async def test_deleting_and_erasing_remove_the_vectors_and_reindex_rebuilds_them(store):
    library = Library(store, embedder=FakeEmbedder())
    a = await library.add(handbook(), "a.pdf", collection=C, sha256="aa" * 32)
    assert await store.vectors.get([f"{a.document}:1:0"], collection=C)
    assert await library.reindex(collection=C) == 1
    assert await store.vectors.get([f"{a.document}:1:0"], collection=C), "reindex rebuilt the chunks from the bundle"
    assert await library.delete(collection=C, document=a.document)
    assert await store.vectors.get([f"{a.document}:1:0"], collection=C) == []
    b = await library.add(handbook(), "b.pdf", collection=C, sha256="bb" * 32)
    assert await library.erase_under("tenants/acme") == 1
    assert await store.vectors.get([f"{b.document}:1:0"], collection=C) == [] and await store.vectors.space_of(C) is None


async def test_an_embedder_by_url_is_a_remote_embedder(store):
    from substrate.models.remote import RemoteEmbedder, RemoteReranker

    library = Library(store, embedder="http://embedding-reranker:8080", reranker="http://embedding-reranker:8080")
    assert isinstance(library._embedder, RemoteEmbedder) and isinstance(library._reranker, RemoteReranker)
    assert library._embedder.url == "http://embedding-reranker:8080"


def test_media_blocks_are_what_a_vision_embedder_is_handed():
    assert MediaBlock.image(data=b"x", media_type="image/png").type == "image"
