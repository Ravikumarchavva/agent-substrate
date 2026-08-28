"""ask() — query the ingested KB: catalog-aware filtering, retrieval, rerank, answer.

Two distinct filter layers — a real design decision, not incidental:

  - ``user_filter``: caller-supplied access-control boundary (tenant/user
    scoping, collection ownership, etc). Always applied, and always wins
    over ``kb_filter`` on a key collision. The LLM never sees or decides
    this — it's an external constraint, not a query-understanding concern.

  - ``kb_filter``: decided BY the LLM from a lightweight catalog (source +
    total_pages per document, from ``list_catalog``) BEFORE any embedding
    search happens — one cheap structured-output generation call, not a
    second retrieval round-trip. Lets "what happened on page 3 of the
    Winter Olympics doc" resolve straight to ``{source, page_number}``
    instead of hoping semantic search alone surfaces that exact page.

The multimodal counterpart to ``RAGPipeline.query_with_context`` (pipeline.py)
— that one is text-only via the generic ``EmbeddingClient`` Protocol; this
takes the multimodal ``EmbeddingReranker`` and mixes text + image results.

Usage::

    from substrate.capabilities.knowledge.ask import ask

    result = await ask(
        "What happened at the opening ceremony?",
        store=store, embedder=embedder, llm_client=llm,
        collection="nq-sample",
    )
    print(result.answer)
    for c in result.citations:
        print(c.source, c.page_number, c.score)
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from substrate.kernel.llm.llm import LLMClient
    from substrate.kernel.storage.vector import SearchResult, VectorStore
    from substrate.runtimes.embedding_reranker.service.embedding import (
        EmbeddingReranker,
    )

logger = logging.getLogger(__name__)


@dataclass
class Citation:
    source: str | None
    page_number: int | None
    score: float
    kind: str  # "text" | "image"
    snippet: str
    # Durable refs into the blob store a citation's source document was
    # ingested with (``DocumentIngestPipeline``'s ``image_key``/``pdf_key``
    # metadata) — present whenever the ingesting deployment used one,
    # regardless of whether *this* ask() call was given a blob_store to
    # resolve them. Lets a caller build its own link/fetch even when
    # rehydration below wasn't requested.
    image_key: str | None = None
    pdf_key: str | None = None
    # Populated only when ask() is given a blob_store and resolving
    # image_key succeeds — see _rehydrate_citation. None otherwise (no
    # blob_store, a text citation, or a failed/missing lookup); the
    # snippet/image_key above still carry provenance either way.
    image_data: bytes | None = None


@dataclass
class AskResult:
    answer: str
    citations: list[Citation] = field(default_factory=list)
    kb_filter: dict[str, Any] | None = None


class _KBFilterDecision(BaseModel):
    """Structured output for the LLM's catalog-based filter decision."""

    source: str | None = None
    page_number: int | None = None


def _is_text(result: SearchResult) -> bool:
    """Text vs. image classification for split-then-rerank below.

    Reads ``metadata["kind"]`` (set by ``DocumentIngestPipeline`` at ingest
    time) when present, rather than the content block's own type. Real,
    found-not-assumed reason this matters: when a blob store is configured,
    an image row's content is a *caption* ``TextBlock`` (see
    ``DocumentIngestPipeline._embed_images``), not an ``ImageBlock`` — the
    block-type check alone would misclassify every stored-not-inlined image
    as a text result. Falls back to the old block-type check for rows
    without ``kind`` (pre-existing collections, or a different producer).
    """
    kind = result.metadata.get("kind")
    if kind is not None:
        return kind == "text"
    return any(block.type == "text" for block in result.content)


async def list_catalog(
    store: VectorStore,
    *,
    collection: str,
    probe_embedding: list[float],
) -> list[dict[str, Any]]:
    """List distinct ``(source, total_pages)`` pairs in a collection.

    ``VectorStore`` has no "list everything" primitive — it's inherently a
    query-driven ANN Protocol — so this reuses ``search()`` with a large
    limit instead of widening the kernel Protocol for one caller's
    convenience. ``probe_embedding`` can be any real embedding already on
    hand (e.g. the question's own, reused here at zero extra cost); its
    actual similarity values are discarded, only the returned rows'
    metadata is used.
    """
    results = await store.search(probe_embedding, collection=collection, limit=10_000)
    seen: dict[str, int] = {}
    for r in results:
        source = r.metadata.get("source")
        if source and source not in seen:
            seen[source] = r.metadata.get("total_pages", 0)
    return [{"source": s, "total_pages": p} for s, p in seen.items()]


async def _decide_kb_filter(
    llm_client: LLMClient,
    question: str,
    catalog: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Ask the LLM whether *question* targets one specific known document/page.

    No embedding call — one cheap structured-output generation over the
    catalog listing only. Returns ``None`` (no filter — normal
    collection-wide semantic search) if the question is general, the
    catalog is empty, or the model's output doesn't parse.
    """
    if not catalog:
        return None

    from substrate.kernel.core.content import ChatMessage, TextBlock
    from substrate.kernel.llm.llm import GenerationOptions

    catalog_text = "\n".join(
        f"- {c['source']} ({c['total_pages']} pages)" for c in catalog
    )
    prompt = (
        "You are deciding whether to narrow a document search to one specific "
        "document and/or page, based on the user's question and this catalog "
        "of available documents:\n\n"
        f"{catalog_text}\n\n"
        f'Question: "{question}"\n\n'
        "Set `source` ONLY if the question itself names or closely paraphrases "
        "one of these exact catalog entries — never infer a document from "
        "topic/content alone, even if you recognize the subject matter; "
        "you don't actually know what's inside these files, only their "
        "names. Set `page_number` only if the question states a specific "
        "page number. A general question that names no document at all "
        "must return both fields null — that is the common case, not the "
        "exception."
    )
    response = await llm_client.generate(
        [ChatMessage(role="user", content=[TextBlock(text=prompt)])],
        options=GenerationOptions(
            response_format=_KBFilterDecision,
            # Real, found-not-assumed: reasoning-capable local models (e.g.
            # Qwen3.5) burn their entire token budget on chain-of-thought
            # before ever emitting the JSON decision, leaving nothing for
            # the actual answer even at 512 tokens — this decision doesn't
            # benefit from reasoning anyway, it's a mechanical lookup
            # against the catalog. `chat_template_kwargs` is llama-server's
            # accepted extra_body field for this; harmless no-op on
            # providers that don't read it (verified: the OpenAI
            # Responses-API client ignores `options.extra` entirely).
            extra={"chat_template_kwargs": {"enable_thinking": False}},
        ),
    )
    from substrate.kernel.core.content import content_blocks_to_str

    text = content_blocks_to_str(response.content).strip()
    try:
        # Parse only the first complete JSON object and ignore anything
        # after it, rather than the whole string. Real, found-not-assumed:
        # llama-server's generic json_object mode (no exact schema, just
        # "valid JSON") doesn't reliably stop after one object — observed
        # it duplicate the same decision twice in a row
        # (`{...}\n{...}`), which `model_validate_json` on the raw string
        # rejects outright as invalid JSON even though the first object is
        # perfectly fine on its own.
        obj, _ = json.JSONDecoder().raw_decode(text)
        decision = _KBFilterDecision.model_validate(obj)
    except Exception as exc:
        logger.warning("kb_filter decision did not parse (%s): %r", exc, text[:200])
        return None

    filter_dict: dict[str, Any] = {}
    if decision.source:
        filter_dict["source"] = decision.source
    if decision.page_number is not None:
        filter_dict["page_number"] = decision.page_number
    return filter_dict or None


async def _rehydrate_citation(citation: Citation, blob_store: Any) -> Citation:
    """Resolve ``image_key`` to real bytes on the way out, mirroring
    ``backends/local.py::_rehydrate_image`` — resolution happens here, per
    read, rather than being baked into the stored row, so a durable ref
    never goes stale and nothing holds an expiring URL. Degrades to the
    citation unchanged (caption/snippet still present) on any failure or a
    missing key: a citation whose image can't be fetched should read like a
    text-only citation, not fail the whole answer."""
    if citation.image_key is None:
        return citation
    from dataclasses import replace

    try:
        data = await blob_store.download(citation.image_key)
    except Exception as exc:
        logger.warning("Loading citation image %s failed: %s", citation.image_key, exc)
        return citation
    return replace(citation, image_data=data)


async def ask(
    question: str,
    *,
    store: VectorStore,
    embedder: EmbeddingReranker,
    llm_client: LLMClient,
    collection: str,
    user_filter: dict[str, Any] | None = None,
    use_kb_filter: bool = True,
    top_k: int = 5,
    rerank: bool = True,
    blob_store: Any | None = None,
) -> AskResult:
    """Answer *question* against *collection*.

    Flow: (optional) LLM decides a ``kb_filter`` from the document catalog
    -> embed the question -> vector search with ``user_filter`` +
    ``kb_filter`` merged (user_filter wins on collision) -> (optional)
    rerank the text results -> LLM generates an answer from the retrieved
    context, citing each piece by its ``[N]`` number.

    Reranking only applies to text results — ``EmbeddingReranker.rerank``
    is not confirmed to support image-aware scoring (see its own
    docstring); image results keep their cosine order.

    ``blob_store``: optional, matching ``DocumentIngestPipeline``'s own
    duck-typed object store (``async download(key) -> bytes``). When given,
    each image citation's ``image_key`` is resolved to real bytes
    (``Citation.image_data``) before returning — omit it to get citations
    with just the durable refs (``image_key``/``pdf_key``) and resolve them
    yourself, e.g. from a serving layer closer to the actual response.
    """
    from substrate.kernel.core.content import (
        ChatMessage,
        TextBlock,
        content_blocks_to_str,
    )
    from substrate.kernel.llm.llm import GenerationOptions
    from substrate.runtimes.embedding_reranker.service.embedding import (
        EmbeddingServiceError,
    )

    query_vec = await embedder.embed_text(question)

    kb_filter: dict[str, Any] | None = None
    if use_kb_filter:
        catalog = await list_catalog(
            store, collection=collection, probe_embedding=query_vec
        )
        kb_filter = await _decide_kb_filter(llm_client, question, catalog)

    # user_filter is an access-control boundary — it always wins over
    # whatever the agent-decided kb_filter says, never the other way round.
    merged_filter = {**(kb_filter or {}), **(user_filter or {})}

    fetch_n = top_k * 4 if rerank else top_k
    results = await store.search(
        query_vec,
        collection=collection,
        limit=fetch_n,
        filter=merged_filter or None,
    )

    text_results = [r for r in results if _is_text(r)]
    image_results = [r for r in results if not _is_text(r)]

    if rerank and text_results:
        try:
            scores = await embedder.rerank(
                question, [r.to_text() for r in text_results]
            )
            text_results = [
                r
                for _, r in sorted(
                    zip(scores, text_results), key=lambda pair: pair[0], reverse=True
                )
            ]
        except EmbeddingServiceError as exc:
            logger.warning("Rerank failed (%s) — keeping cosine-similarity order", exc)

    final = (text_results + image_results)[:top_k]

    citations = [
        Citation(
            source=r.metadata.get("source"),
            page_number=r.metadata.get("page_number"),
            score=r.score,
            kind="text" if _is_text(r) else "image",
            snippet=r.to_text()[:200],
            image_key=r.metadata.get("image_key"),
            pdf_key=r.metadata.get("pdf_key"),
        )
        for r in final
    ]
    if blob_store is not None:
        citations = list(
            await asyncio.gather(
                *(_rehydrate_citation(c, blob_store) for c in citations)
            )
        )

    context = "\n\n".join(
        f"[{i + 1}] (source={c.source}, page={c.page_number})\n{c.snippet}"
        for i, c in enumerate(citations)
    )
    prompt = (
        "Answer the question using only the context below. Cite sources by "
        f"their [N] number.\n\nContext:\n{context}\n\nQuestion: {question}"
    )
    response = await llm_client.generate(
        [ChatMessage(role="user", content=[TextBlock(text=prompt)])],
        options=GenerationOptions(),
    )
    answer = content_blocks_to_str(response.content)

    return AskResult(answer=answer, citations=citations, kb_filter=kb_filter)


__all__ = ["ask", "AskResult", "Citation", "list_catalog"]
