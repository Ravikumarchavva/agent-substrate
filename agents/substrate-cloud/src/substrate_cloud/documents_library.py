"""The platform's documents: ``Library`` over the one ``Store`` and the object store.

* A **conversation's documents** (uploads) are filed in ``conversation_documents_prefix(tenant, user, conversation)`` and searched by words —
  a few documents the user just gave the model need no embeddings.
* A **knowledge base** (the organisation's documents) is a collection under ``knowledge_collection(tenant, kb)``; it has an embedder and a
  reranker when one is configured (``EMBEDDING_RERANKER_SERVICE_URL``: Qwen3-VL by URL; or ``EMBEDDING_MODEL``), and is searched by meaning
  and words, and by words alone when not.

Which collection a run reaches comes from its scope — the authenticated tenant, user and conversation — never from anything the model says.
"""

from __future__ import annotations

import logging
from typing import Any

from substrate.documents import Library
from substrate.types.run import RunScope
from substrate.workspace.layout import conversation_documents_prefix, knowledge_collection

from substrate_cloud.document_reader import document_reader

logger = logging.getLogger(__name__)


def documents_collection(tenant_id: str | None, user_id: str | None, thread_id: str | None) -> str | None:
    """The collection of one conversation's documents, or ``None`` if the identity is incomplete (or not a valid id)."""
    if not (tenant_id and user_id and thread_id):
        return None
    try:
        return conversation_documents_prefix(tenant_id, user_id, thread_id)
    except ValueError:
        return None


def collection_for_scope(scope: RunScope) -> str | None:
    return documents_collection(scope.tenant_id, scope.user_id, scope.thread_id)


def knowledge_collection_for(tenant_id: str | None, knowledge_base_id: str) -> str | None:
    """The collection of one tenant's knowledge base, or ``None`` if either id is missing or not a valid id."""
    if not (tenant_id and knowledge_base_id):
        return None
    try:
        return knowledge_collection(tenant_id, knowledge_base_id)
    except ValueError:
        return None


def knowledge_collection_for_scope(scope: RunScope, knowledge_base_id: str) -> str | None:
    return knowledge_collection_for(scope.tenant_id, knowledge_base_id)


def build_library(store: Any, file_store: Any, cfg: Any) -> Library | None:
    """The conversation documents' library; ``None`` when there is no store to keep the catalog in or no object store to keep the bundle in."""
    if store is None or file_store is None:
        return None
    return Library(store, files=file_store, reader=document_reader(cfg))


def build_embedder(cfg: Any) -> Any:
    """The knowledge bases' embedder, or ``None`` when none is configured (they are then searched by words).

    The embedding service by URL is the default; ``EMBEDDING_MODEL`` names a provider model instead. A provider that cannot be built
    (no key, a library not installed) is logged and means no embedder — never a server that fails to start."""
    if cfg.EMBEDDING_RERANKER_SERVICE_URL:
        from substrate.models.remote import RemoteEmbedder

        return RemoteEmbedder(cfg.EMBEDDING_RERANKER_SERVICE_URL, api_key=cfg.EMBEDDING_RERANKER_AUTH_TOKEN, timeout=float(cfg.EMBEDDING_RERANKER_TIMEOUT_S))
    if cfg.EMBEDDING_MODEL:
        try:
            from substrate.integrations.llm.factory import create_embedding_client

            return create_embedding_client(cfg.EMBEDDING_MODEL)
        except Exception as exc:  # noqa: BLE001
            logger.warning("embedding model %r unavailable, knowledge bases will be searched by words only: %s", cfg.EMBEDDING_MODEL, exc)
    return None


def build_reranker(cfg: Any) -> Any:
    if cfg.EMBEDDING_RERANKER_SERVICE_URL:
        from substrate.models.remote import RemoteReranker

        return RemoteReranker(cfg.EMBEDDING_RERANKER_SERVICE_URL, api_key=cfg.EMBEDDING_RERANKER_AUTH_TOKEN, timeout=float(cfg.EMBEDDING_RERANKER_TIMEOUT_S))
    return None


def build_knowledge_library(store: Any, file_store: Any, cfg: Any) -> Library | None:
    """The knowledge bases' library: the same store and object store, with the configured embedder and reranker."""
    if store is None or file_store is None:
        return None
    return Library(store, files=file_store, reader=document_reader(cfg), embedder=build_embedder(cfg), reranker=build_reranker(cfg))


__all__ = [
    "build_embedder",
    "build_knowledge_library",
    "build_library",
    "build_reranker",
    "collection_for_scope",
    "documents_collection",
    "knowledge_collection_for",
    "knowledge_collection_for_scope",
]
