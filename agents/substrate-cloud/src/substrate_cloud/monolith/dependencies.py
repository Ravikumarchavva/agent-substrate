"""Typed container for server-wide shared dependencies.

Routes can access these via ``request.app.state.ctx`` for type-safe
attribute access instead of the dynamic ``app.state.*`` bag.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from fastapi import Request

from substrate.models import ChatModel
from substrate.stores import ThreadStore
from substrate_cloud.monolith.sse.bridge import BridgeRegistry


@dataclass
class ServerDependencies:
    """All shared dependencies available to route handlers.

    ``cancel_registry``/``thread_locks`` (per-process cancel Events and
    single-flight asyncio.Locks) were removed — both single-flight and
    cancel are now enforced durably by the Runtime itself (a unique index
    on ``run_queue`` and ``SupervisorProtocol.cancel()`` respectively), which
    holds correctly across replicas instead of only within one process. See
    ``routes/chat.py`` and ``routes/cancel.py``.
    """

    model_client: ChatModel
    history: ThreadStore
    tools: Any
    bridge_registry: BridgeRegistry
    tools_requiring_approval: list[str]
    system_instructions: str
    tool_timeout: float
    model_client_kwargs: dict[str, Any] = field(default_factory=dict)
    api_keys: dict[str, str] = field(default_factory=dict)
    runtime: Optional[Any] = None
    mcp_servers: dict[str, dict] = field(default_factory=dict)
    session_factory: Any = None
    ci_client: Optional[Any] = None
    # The process-wide store for consumers built once (documents, artifacts, the sandbox's blobs). Request code uses ``files_for``.
    file_store: Optional[Any] = None
    # Which store a caller gets, and as whom (substrate_cloud.file_access.FileAccess); set whenever there is a file store.
    file_access: Optional[Any] = None
    trigger_scheduler: Optional[Any] = None
    short_term_memory: Optional[Any] = None
    long_term_memory: Optional[Any] = None
    workspace_user_quota_bytes: int = 1024 * 1024 * 1024
    workspace_user_delete_allowed: bool = True
    safety_middleware: Optional[Any] = None
    # The organisation's knowledge bases: the same kind of Library, with the configured embedder and reranker (searched by words when
    # none is configured).
    knowledge: Optional[Any] = None
    # The documents a conversation was given, filed as an OKF bundle with a catalog (substrate.documents.Library); the model works
    # through them with the `documents` tool. `None` when there is no store or object store to keep them in.
    library: Optional[Any] = None
    # Writes descriptions for documents in the background after they are added (documents_library.EnrichmentQueue); `None` when disabled
    # or there is no model key for it.
    enrichment: Optional[Any] = None
    # Curated OKF bundles (integrations/artifacts/) at session and global
    # scope. Shares `file_store`'s bucket but a different key prefix — the
    # sandbox never mounts it, so nothing lands here without an explicit
    # save or promotion.
    artifact_store: Optional[Any] = None
    # Branch-isolated workspace snapshot store (kernel WorkspaceStore) — see
    # agents/workspace/. Forking a branch's workspace goes through this,
    # not file_store.copy_prefix.
    workspace_store: Optional[Any] = None

    # -- the file store for one caller ------------------------------------------------------------
    # Request code never touches ``file_store`` directly. ``files_for`` hands it the store for that caller: in the data service
    # the caller's own delegated token (so the ownership rule is enforced there), locally the shared folder fenced to
    # ``tenants/<tenant>/`` (stores/scoped.py). ``user_id`` is whose files these are: the caller's, or for an admin reading a
    # conversation, its owner's.

    def files_for(
        self, tenant_id: str | None, user_id: str | None = None
    ) -> Any | None:
        if self.file_store is None and self.file_access is None:
            return None
        if not tenant_id:
            raise ValueError("an object store can only be used on behalf of a tenant")
        if self.file_access is not None:
            return self.file_access.for_user(tenant_id, user_id)
        from substrate.types import Scope
        from substrate.stores import fence_objects

        return fence_objects(self.file_store, Scope(tenant_id=tenant_id))

    def direct_files_for(self, tenant_id: str, user_id: str) -> Any | None:
        """The caller's store with direct-upload tickets, versions and quotas; None when files are a local folder."""
        return (
            self.file_access.direct(tenant_id, user_id)
            if self.file_access is not None
            else None
        )


def get_ctx(request: Request) -> ServerDependencies:
    """FastAPI dependency that returns typed server dependencies."""
    return request.app.state.ctx  # type: ignore[return-value]
