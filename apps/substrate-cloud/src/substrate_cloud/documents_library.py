"""The platform's documents: one ``Library`` over the one ``Store`` and the object store, one collection per conversation.

A conversation's uploaded documents are filed in ``conversation_documents_prefix(tenant, user, conversation)``. The model works through them
with the ``documents`` tool, and which collection that is comes from the run's scope — the authenticated tenant, user and conversation —
never from anything the model says.
"""

from __future__ import annotations

from typing import Any

from substrate.documents import Library
from substrate.types.run import RunScope
from substrate.workspace.layout import conversation_documents_prefix

from substrate_cloud.document_reader import document_reader


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


def build_library(store: Any, file_store: Any, cfg: Any) -> Library | None:
    """``None`` when there is no store to keep the catalog in or no object store to keep the bundle in."""
    if store is None or file_store is None:
        return None
    return Library(store, files=file_store, reader=document_reader(cfg))


__all__ = ["build_library", "collection_for_scope", "documents_collection"]
