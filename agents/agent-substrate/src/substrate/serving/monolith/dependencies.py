"""Typed container for server-wide shared dependencies.

Routes can access these via ``request.app.state.ctx`` for type-safe
attribute access instead of the dynamic ``app.state.*`` bag.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from fastapi import Request

from substrate.kernel.llm import LLMClient
from substrate.kernel.storage.history import HistoryProvider
from substrate.serving.monolith.sse.bridge import BridgeRegistry


@dataclass
class ServerDependencies:
    """All shared dependencies available to route handlers.

    ``cancel_registry``/``thread_locks`` (per-process cancel Events and
    single-flight asyncio.Locks) were removed — both single-flight and
    cancel are now enforced durably by the Runtime itself (a unique index
    on ``substrate_run_queue`` and ``SupervisorProtocol.cancel()`` respectively), which
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


def get_ctx(request: Request) -> ServerDependencies:
    """FastAPI dependency that returns typed server dependencies."""
    return request.app.state.ctx  # type: ignore[return-value]
