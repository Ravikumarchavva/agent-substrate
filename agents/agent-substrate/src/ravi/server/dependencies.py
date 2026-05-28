"""Typed container for server-wide shared dependencies.

Routes can access these via ``request.app.state.ctx`` for type-safe
attribute access instead of the dynamic ``app.state.*`` bag.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional

from fastapi import Request

from ravi.integrations.memory.redis_memory import RedisMemory
from ravi.kernel.runtime import AgentRuntime
from ravi.kernel.storage.base import FileStore
from ravi.fabric.catalog import AgentCatalogRegistry
from ravi.kernel.llm.base_client import BaseModelClient
from ravi.server.sse.bridge import BridgeRegistry


@dataclass
class ServerDependencies:
    """All shared dependencies available to route handlers."""

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


def get_ctx(request: Request) -> ServerDependencies:
    """FastAPI dependency that returns typed server dependencies."""
    return request.app.state.ctx  # type: ignore[return-value]
