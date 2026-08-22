"""End-to-end retrieval eval against a real Postgres + real embedding calls
(``OpenAIEmbeddingClient``, ``text-embedding-3-small`` — same as the app's
default ``EMBEDDING_MODEL``). Skips if Postgres isn't reachable or
``OPENAI_API_KEY`` isn't set — this is a real infra-dependent integration
test, not a unit test, same convention as ``tests/capabilities/test_pg_backends.py``.

Per the plan's §Evaluation harness / Verification point 5: **this is a
baseline measurement, not a target** — the point is confirming every
per-stage metric is computed and sane (0.0-1.0, hybrid recall generally at
or above whichever of dense/lexical alone found more), not hitting a score.
Numbers are printed via `-s` for a human to look at; assertions only check
shape/sanity so this doesn't become a flaky threshold test on a 12-query
starter set.

To grow this into the plan's real ~200-500 query target: replace
``dataset.build_starter_dataset()`` with a loader over a real labeled set
(ingest real documents — the Apple 10-Q used elsewhere this session is a
natural starting corpus — and hand-label relevant chunk ids per query), then
call ``run_retrieval_eval`` the same way. The runner itself doesn't change.
The image-category queries here use a chart/table caption as their
document text — the real image path (Qwen3-VL embeddings) additionally
needs the ``llama-embed`` sidecar (``docker compose --profile doc-handler
up``) and an ``image_store`` argument passed through the same way; not
covered by this file today.
"""

from __future__ import annotations

import os
from dataclasses import replace

import pytest

from tests.eval.dataset import build_starter_dataset
from tests.eval.runner import run_retrieval_eval

_PG_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/agentdb"
)


async def _pg_engine():
    try:
        from sqlalchemy.ext.asyncio import create_async_engine

        url = _PG_URL.replace("postgresql://", "postgresql+asyncpg://")
        engine = create_async_engine(url, pool_pre_ping=True)
        async with engine.connect():
            pass
        return engine
    except Exception:
        return None


async def test_retrieval_eval_starter_dataset(capsys) -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")
    engine = await _pg_engine()
    if engine is None:
        pytest.skip("Postgres not reachable")

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from substrate.capabilities.vector.pgvector_store import PgVectorStore
    from substrate.integrations.llm.openai.openai_embedding_client import (
        OpenAIEmbeddingClient,
    )

    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    # A dedicated table, not the shared default one other tests in this
    # suite reuse — text-embedding-3-small is 1536-dim, and a real
    # deployment's default table may already exist at a different width
    # (see test_pg_backends.py's isolation test for why that matters).
    store = PgVectorStore(
        session_factory=session_factory,
        engine=engine,
        dimensions=1536,
        table_name="vector_documents_eval_test",
    )
    await store.ensure_table()

    embedding_client = OpenAIEmbeddingClient(model="text-embedding-3-small")
    dataset = build_starter_dataset()
    collection = f"eval-{id(object())}"

    try:
        embedded_docs = []
        for doc in dataset.documents:
            vector = await embedding_client.embed_single(doc.to_text())
            embedded_docs.append(replace(doc, embedding=vector))
        await store.add(embedded_docs, collection=collection)

        report = await run_retrieval_eval(
            store=store,
            embedding_client=embedding_client,
            dataset=dataset,
            collection=collection,
            # Starter dataset has ~10 documents total — budgets are capped
            # to it rather than the plan's production defaults (dense_k=50
            # etc. would just mean "every document", making every recall
            # number trivially 1.0 and testing nothing).
            dense_k=10,
            lexical_k=10,
            fused_k=10,
            rerank_top_n=5,
            final_k=3,
        )

        with capsys.disabled():
            print("\n--- Retrieval eval (starter dataset, baseline — not a target) ---")
            print(f"Overall  ({report.overall.n_queries} queries): {report.overall}")
            for category, metrics in sorted(report.by_category.items()):
                print(f"  {category:>5} ({metrics.n_queries} queries): {metrics}")

        # Baseline measurement, not a target (see module docstring) — the
        # only thing asserted is that every metric actually computed to a
        # sane value, so a wiring bug (e.g. an empty candidate list silently
        # scoring 0.0 everywhere) fails loudly instead of just printing a
        # suspiciously flat report.
        for stage_metrics in [report.overall, *report.by_category.values()]:
            assert stage_metrics.n_queries > 0
            for value in (
                stage_metrics.dense_recall,
                stage_metrics.lexical_recall,
                stage_metrics.hybrid_recall,
                stage_metrics.prefilter_recall,
                stage_metrics.reranker_ndcg,
                stage_metrics.final_recall,
            ):
                assert 0.0 <= value <= 1.0
    finally:
        await store.delete_collection(collection)
