"""Typed container for server-wide shared dependencies.

Routes can access these via ``request.app.state.ctx`` for type-safe
attribute access instead of the dynamic ``app.state.*`` bag.

Example::

    from agent_framework.server.context import ServerContext, get_ctx

    ctx: ServerContext = Depends(get_ctx)
    agent = ReActAgent(model_client=ctx.model_client, ...)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional

from fastapi import Request

from agent_framework.core.memory.redis_memory import RedisMemory
from agent_framework.core.tools.registry import ToolRegistry
from agent_framework.providers.llm.openai.openai_client import OpenAIClient
from agent_framework.providers.audio.openai import OpenAIAudioClient
from agent_framework.runtime.hitl import BridgeRegistry


@dataclass
class ServerContext:
    """All shared dependencies available to route handlers.

    Attributes:
        model_client: The default LLM client used to construct agents.
        audio_client: Provider-agnostic audio client (transcription / TTS / realtime).
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
    """

    model_client: OpenAIClient
    audio_client: OpenAIAudioClient
    redis_memory: RedisMemory
    tools: ToolRegistry
    bridge_registry: BridgeRegistry
    tools_requiring_approval: list[str]
    system_instructions: str
    tool_timeout: float
    cancel_registry: dict[str, Any] = field(default_factory=dict)
    thread_locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    mcp_servers: dict[str, dict] = field(default_factory=dict)
    session_factory: Any = None
    ci_client: Optional[Any] = None


def get_ctx(request: Request) -> ServerContext:
    """FastAPI dependency that returns the typed ``ServerContext``.

    Usage in route handlers::

        @router.get("/something")
        async def handler(ctx: ServerContext = Depends(get_ctx)):
            client = ctx.model_client
            ...
    """
    return request.app.state.ctx  # type: ignore[return-value]
