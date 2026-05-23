"""Typed container for server-wide shared dependencies.

Routes can access these via ``request.app.state.ctx`` for type-safe
attribute access instead of the dynamic ``app.state.*`` bag.

Example::

    from ravi.server.context import ServerContext, get_ctx

    ctx: ServerContext = Depends(get_ctx)
    catalog = AgentCatalog()
    catalog.register_model("primary", ctx.model_client)
    agent = ReActAgent(catalog=catalog, ...)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional

from fastapi import Request

from ravi.integrations.memory.redis_memory import RedisMemory
from ravi.core.runtime import AgentRuntime
from ravi.core.storage.base import FileStore
from ravi.core.agent_catalog import AgentCatalogRegistry
from ravi.core.llm.base_client import BaseModelClient
from ravi.server.sse.bridge import BridgeRegistry


@dataclass
class ServerContext:
    """All shared dependencies available to route handlers.

    Attributes:
        model_client: The default LLM client (text, vision, STT, TTS, Realtime S2S, image gen).
        model_client_kwargs: Shared factory kwargs used for per-request model selection.
        redis_memory: Global Redis memory factory (connect/disconnect lifecycle).
        tools: Registry of all available agent tools.
        bridge_registry: Per-thread SSE event bus registry (HITL, streaming).
        tools_requiring_approval: Names of tools that require HITL approval.
        system_instructions: Default system prompt loaded from prompts/default_system.md.
        tool_timeout: Seconds to wait before declaring a tool call timed-out.
        cancel_registry: Maps thread_id → asyncio.Event for request cancellation.
        thread_locks: Maps thread_id → asyncio.Lock for single-flight per-thread.
        mcp_servers: Runtime MCP server registry (populated via /builder API).
        session_factory: SQLAlchemy async session factory for DB access.
        ci_client: Optional code-interpreter HTTP client.
        file_store: Pluggable file storage backend (local / S3 / encrypted).
    """

    model_client: BaseModelClient
    redis_memory: RedisMemory
    tools: AgentCatalogRegistry
    bridge_registry: BridgeRegistry
    tools_requiring_approval: list[str]
    system_instructions: str
    tool_timeout: float
    model_client_kwargs: dict[str, Any] = field(default_factory=dict)
    api_keys: dict[str, str] = field(default_factory=dict)
    runtime: Optional[AgentRuntime] = None
    cancel_registry: dict[str, Any] = field(default_factory=dict)
    thread_locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    mcp_servers: dict[str, dict] = field(default_factory=dict)
    session_factory: Any = None
    ci_client: Optional[Any] = None
    file_store: Optional[FileStore] = None


def get_ctx(request: Request) -> ServerContext:
    """FastAPI dependency that returns the typed ``ServerContext``.

    Usage in route handlers::

        @router.get("/something")
        async def handler(ctx: ServerContext = Depends(get_ctx)):
            client = ctx.model_client
            ...
    """
    return request.app.state.ctx  # type: ignore[return-value]
