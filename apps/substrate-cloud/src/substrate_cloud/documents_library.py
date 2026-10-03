"""The platform's documents: ``Library`` over the one ``Store`` and the object store.

* A **conversation's documents** (uploads) are filed in ``conversation_documents_prefix(tenant, user, conversation)`` and searched by words —
  a few documents the user just gave the model need no embeddings.
* A **knowledge base** (the organisation's documents) is a collection under ``knowledge_collection(tenant, kb)``; it has an embedder and a
  reranker when one is configured (``EMBEDDING_RERANKER_SERVICE_URL``: Qwen3-VL by URL; or ``EMBEDDING_MODEL``), and is searched by meaning
  and words, and by words alone when not.

Which collection a run reaches comes from its scope — the authenticated tenant, user and conversation — never from anything the model says.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from substrate.documents import (
    Enricher,
    LLMEnricher,
    LLMOrganiser,
    Library,
    Organiser,
)
from substrate.types.run import RunScope
from substrate.workspace.layout import (
    conversation_documents_prefix,
    knowledge_collection,
)

from substrate_cloud.document_reader import document_reader

logger = logging.getLogger(__name__)


def documents_collection(
    tenant_id: str | None, user_id: str | None, thread_id: str | None
) -> str | None:
    """The collection of one conversation's documents, or ``None`` if the identity is incomplete (or not a valid id)."""
    if not (tenant_id and user_id and thread_id):
        return None
    try:
        return conversation_documents_prefix(tenant_id, user_id, thread_id)
    except ValueError:
        return None


def collection_for_scope(scope: RunScope) -> str | None:
    return documents_collection(scope.tenant_id, scope.user_id, scope.thread_id)


def knowledge_collection_for(
    tenant_id: str | None, knowledge_base_id: str
) -> str | None:
    """The collection of one tenant's knowledge base, or ``None`` if either id is missing or not a valid id."""
    if not (tenant_id and knowledge_base_id):
        return None
    try:
        return knowledge_collection(tenant_id, knowledge_base_id)
    except ValueError:
        return None


def knowledge_collection_for_scope(
    scope: RunScope, knowledge_base_id: str
) -> str | None:
    return knowledge_collection_for(scope.tenant_id, knowledge_base_id)


def build_enricher(
    cfg: Any, api_keys: dict[str, str], **client_kwargs: Any
) -> Enricher | None:
    """The model that describes documents, or ``None`` when that is off or has no key (descriptions are then not written; nothing else changes).

    Uses ``DOCUMENT_SUMMARY_MODEL``, else ``CHAT_MODEL``, with the platform's keys. A model that cannot be built is logged, never a server that
    fails to start."""
    if not getattr(cfg, "DOCUMENT_SUMMARY_ENABLED", True):
        return None
    model = cfg.DOCUMENT_SUMMARY_MODEL or cfg.CHAT_MODEL
    try:
        from substrate.integrations.llm.factory import (
            create_model_client,
            detect_provider,
            has_provider_api_key,
        )

        if not has_provider_api_key(detect_provider(model), api_keys):
            logger.info("document descriptions are off: no API key for %s", model)
            return None
        client = create_model_client(
            model, api_keys=api_keys, temperature=0.2, **client_kwargs
        )
        return LLMEnricher(
            client, concurrency=int(cfg.DOCUMENT_SUMMARY_CONCURRENCY) * 2
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "document descriptions are off: could not build %s: %s", model, exc
        )
        return None


def build_organiser(enricher: Enricher | None) -> Organiser | None:
    """Whatever redraws a knowledge base's topic tree: the same model that describes its documents, or ``None`` when there is no such model."""
    return LLMOrganiser(enricher.model) if isinstance(enricher, LLMEnricher) else None


def build_library(
    store: Any, file_store: Any, cfg: Any, enricher: Enricher | None = None
) -> Library | None:
    """The conversation documents' library; ``None`` when there is no store to keep the catalog in or no object store to keep the bundle in."""
    if store is None or file_store is None:
        return None
    return Library(
        store,
        files=file_store,
        reader=document_reader(cfg),
        enricher=enricher,
        enrich_timeout=float(cfg.DOCUMENT_SUMMARY_TIMEOUT_S),
    )


def build_embedder(cfg: Any) -> Any:
    """The knowledge bases' embedder, or ``None`` when none is configured (they are then searched by words).

    The embedding service by URL is the default; ``EMBEDDING_MODEL`` names a provider model instead. A provider that cannot be built
    (no key, a library not installed) is logged and means no embedder — never a server that fails to start."""
    if cfg.EMBEDDING_RERANKER_SERVICE_URL:
        from substrate.models.remote import RemoteEmbedder

        return RemoteEmbedder(
            cfg.EMBEDDING_RERANKER_SERVICE_URL,
            api_key=cfg.EMBEDDING_RERANKER_AUTH_TOKEN,
            timeout=float(cfg.EMBEDDING_RERANKER_TIMEOUT_S),
        )
    if cfg.EMBEDDING_MODEL:
        try:
            from substrate.integrations.llm.factory import create_embedding_client

            return create_embedding_client(cfg.EMBEDDING_MODEL)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "embedding model %r unavailable, knowledge bases will be searched by words only: %s",
                cfg.EMBEDDING_MODEL,
                exc,
            )
    return None


def build_reranker(cfg: Any) -> Any:
    if cfg.EMBEDDING_RERANKER_SERVICE_URL:
        from substrate.models.remote import RemoteReranker

        return RemoteReranker(
            cfg.EMBEDDING_RERANKER_SERVICE_URL,
            api_key=cfg.EMBEDDING_RERANKER_AUTH_TOKEN,
            timeout=float(cfg.EMBEDDING_RERANKER_TIMEOUT_S),
        )
    return None


def build_knowledge_library(
    store: Any,
    file_store: Any,
    cfg: Any,
    enricher: Enricher | None = None,
    organiser: Organiser | None = None,
) -> Library | None:
    """The knowledge bases' library: the same store and object store, with the configured embedder and reranker, and its documents filed
    under topics (a conversation's handful of documents are not)."""
    if store is None or file_store is None:
        return None
    return Library(
        store,
        files=file_store,
        reader=document_reader(cfg),
        embedder=build_embedder(cfg),
        reranker=build_reranker(cfg),
        enricher=enricher,
        organiser=organiser,
        file_topics=True,
        enrich_timeout=float(cfg.DOCUMENT_SUMMARY_TIMEOUT_S),
    )


def tenant_of(collection: str) -> str:
    """The tenant a collection belongs to (``tenants/{tenant}/…``), or ``""``."""
    parts = collection.split("/")
    return parts[1] if len(parts) > 1 and parts[0] == "tenants" else ""


class EnrichmentQueue:
    """Describes documents in the background, a few at a time, within each tenant's daily token budget.

    ``submit`` returns at once and never raises; the work happens on the event loop. A document already queued is not queued twice. A tenant
    past its daily budget is skipped (its documents stay ``pending`` and are picked up by the next sweep once the day turns)."""

    def __init__(
        self, *, concurrency: int, daily_tokens: int, redis: Any = None
    ) -> None:
        self._slots = asyncio.Semaphore(max(1, concurrency))
        self._daily = daily_tokens
        self._redis = redis
        self._local: dict[str, int] = {}
        self._inflight: set[tuple[str, str]] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    def submit(
        self, library: Library, collection: str, document: str, *, force: bool = False
    ) -> None:
        key = (
            collection,
            document,
        )  # by document, not by library: two libraries over one store would otherwise describe it twice
        if key in self._inflight:
            return
        self._inflight.add(key)
        task = asyncio.create_task(
            self._run(library, collection, document, key, force=force)
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def sweep(
        self,
        library: Library,
        *,
        matching: str = "%",
        excluding: str | None = None,
        limit: int = 200,
    ) -> int:
        """Queue the documents of ``library``'s own collections that still need describing (after a restart, a failure, or a changed model).
        ``matching``/``excluding`` are ``LIKE`` patterns on the collection. Returns how many."""
        pending = await library.pending_all(
            matching=matching, excluding=excluding, limit=limit
        )
        for collection, document in pending:
            self.submit(library, collection, document)
        return len(pending)

    async def drain(self) -> None:
        """Wait for everything queued (shutdown, tests)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def _budget_key(self, tenant: str) -> str:
        return f"enrich:tokens:{tenant}:{time.strftime('%Y%m%d', time.gmtime())}"

    async def _spent(self, tenant: str) -> int:
        key = self._budget_key(tenant)
        if self._redis is not None:
            try:
                return int(await self._redis.get(key) or 0)
            except Exception:  # noqa: BLE001 — a cache outage must not stop descriptions or wrongly block them
                return 0
        return self._local.get(key, 0)

    async def _record(self, tenant: str, tokens: int) -> None:
        key = self._budget_key(tenant)
        if self._redis is not None:
            try:
                await self._redis.incrby(key, tokens)
                await self._redis.expire(key, 3 * 86400)
                return
            except Exception:  # noqa: BLE001
                pass
        self._local[key] = self._local.get(key, 0) + tokens

    async def _redraw_if_due(
        self, library: Library, collection: str, tenant: str
    ) -> None:
        """Once filing one document at a time has left a topic too wide, have the whole tree redrawn in one pass (see ``Library.reorganise``)."""
        if not await library.needs_reorganising(collection):
            return
        if self._daily and await self._spent(tenant) >= self._daily:
            return
        redrawn = await library.reorganise(collection=collection)
        if redrawn.usage.input_tokens or redrawn.usage.output_tokens:
            await self._record(
                tenant, redrawn.usage.input_tokens + redrawn.usage.output_tokens
            )
        logger.info(
            "redrew the topics of %s: %s, %d documents moved, $%.4f",
            collection, redrawn.state, redrawn.moved, redrawn.usage.cost_usd,
        )  # fmt: skip

    async def _run(
        self,
        library: Library,
        collection: str,
        document: str,
        key: tuple[str, str],
        *,
        force: bool = False,
    ) -> None:
        try:
            async with self._slots:
                tenant = tenant_of(collection)
                if self._daily and await self._spent(tenant) >= self._daily:
                    logger.info(
                        "document descriptions paused for tenant %s: daily token budget used",
                        tenant,
                    )
                    return
                done = await library.enrich(
                    collection=collection, document=document, force=force
                )
                await self._redraw_if_due(library, collection, tenant)
                if done.usage.input_tokens or done.usage.output_tokens:
                    await self._record(
                        tenant, done.usage.input_tokens + done.usage.output_tokens
                    )
                    logger.info(
                        "described %s/%s: %s, %d in / %d out tokens, $%.4f",
                        collection, document, done.state, done.usage.input_tokens,
                        done.usage.output_tokens, done.usage.cost_usd,
                    )  # fmt: skip
        except Exception:  # noqa: BLE001 — this is a background job: log it, never raise
            logger.exception("describing %s/%s failed", collection, document)
        finally:
            self._inflight.discard(key)


__all__ = [
    "EnrichmentQueue",
    "build_embedder",
    "build_enricher",
    "build_knowledge_library",
    "build_organiser",
    "build_library",
    "build_reranker",
    "collection_for_scope",
    "documents_collection",
    "knowledge_collection_for",
    "tenant_of",
    "knowledge_collection_for_scope",
]
