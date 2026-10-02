"""Typed container for server-wide shared dependencies.

Routes can access these via ``request.app.state.ctx`` for type-safe
attribute access instead of the dynamic ``app.state.*`` bag.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from fastapi import Request

from substrate.kernel.abstractions.llm import LLMClient
from substrate.kernel.abstractions.storage.history import HistoryProvider
from substrate.serving.monolith.sse.bridge import BridgeRegistry


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

    model_client: LLMClient
    history: HistoryProvider
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
    file_store: Optional[Any] = None
    trigger_scheduler: Optional[Any] = None
    short_term_memory: Optional[Any] = None
    long_term_memory: Optional[Any] = None
    workspace_user_quota_bytes: int = 1024 * 1024 * 1024
    workspace_user_delete_allowed: bool = True
    rag_backend: Optional[Any] = None
    safety_middleware: Optional[Any] = None
    # Shared with rag_backend's own internal RAGPipeline — reused (not
    # duplicated) by the per-user session-document index
    # (integrations/knowledge/session_ingest.py) so both the tenant-KB flow
    # and the per-user flow embed through the same configured model.
    embedding_client: Optional[Any] = None
    # Local disk store for not-yet-sent attachments — see
    # integrations/storage/pending.py. Never SeaweedFS/S3 directly; routes
    # promote a file from here into `file_store` only once the message
    # carrying it is actually sent (routes/chat_context.py).
    pending_file_store: Optional[Any] = None
    # Curated OKF bundles (integrations/artifacts/) at session and global
    # scope. Shares `file_store`'s bucket but a different key prefix — the
    # sandbox never mounts it, so nothing lands here without an explicit
    # save or promotion.
    artifact_store: Optional[Any] = None
    # Branch-isolated workspace snapshot store (kernel WorkspaceStore) — see
    # agents/workspace/. Forking a branch's workspace goes through this,
    # not file_store.copy_prefix.
    workspace_store: Optional[Any] = None

    # -- tenant-fenced object stores -------------------------------------------------------------
    # Request code never touches ``file_store`` / ``pending_file_store`` directly: these return them fenced
    # to one tenant's ``tenants/<tenant>/`` subtree (kernel/storage/scoped.py), so a key built from a
    # database row or a request cannot reach another tenant's objects. The unfenced stores stay for the
    # admin routes, which are tenant-wide by design.

    def files_for(self, tenant_id: str | None) -> Any | None:
        return _fence(self.file_store, tenant_id)

    def pending_for(self, tenant_id: str | None) -> Any | None:
        return _fence(self.pending_file_store, tenant_id)


def _fence(store: Any, tenant_id: str | None) -> Any | None:
    if store is None:
        return None
    if not tenant_id:
        raise ValueError("an object store can only be used on behalf of a tenant")
    from substrate.kernel.abstractions.core.scope import Scope
    from substrate.kernel.storage.scoped import fence_objects

    return fence_objects(store, Scope(tenant_id=tenant_id))


def get_ctx(request: Request) -> ServerDependencies:
    """FastAPI dependency that returns typed server dependencies."""
    return request.app.state.ctx  # type: ignore[return-value]
