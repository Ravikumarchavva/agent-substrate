"""Agent service – creates agents with restored per-session memory.

Responsibilities:
  1. Build agent memory from persisted steps (Redis hot path, Postgres cold path)
  2. Create configured ReActAgent per thread
  3. Real-time write-through to Redis via per-request ``RedisMemory``
  4. Persist new messages to database (Postgres cold store) during streaming

Stateless agent design
──────────────────────
Agents hold **no** state between requests.  Every request:
  1. Creates a per-request ``RedisMemory(session_id=...)`` sharing the
     parent's connection pool (zero new TCP connections).
  2. Loads the full chat history from Redis into local cache via ``restore()``.
     On cache miss, seeds Redis from the Postgres cold store first.
  3. Passes a ``SlidingWindowContext(max_messages=N)`` to the agent — the LLM
     only sees the last N messages, while the full history stays in memory
     and Redis.
  4. Runs the agent — each ``add_message()`` writes through to Redis in
     real-time via a fire-and-forget background task.  No post-run sync needed.

Redis is the source of truth for active sessions.  On the first request for
a thread (cache miss), the Postgres cold store is read to seed Redis.
"""

from __future__ import annotations
from ravi.logger import setup_logging

import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ravi.reasoning.agents.assistant.agent import AssistantAgent
from ravi.kernel.context.base_context import ModelContext
from ravi.reasoning.memory.context.sliding_window import SlidingWindowContext
from ravi.catalog.tools.human_input.tool import ToolApprovalHandler
from ravi.integrations.memory.redis_memory import RedisMemory
from ravi.kernel.messages.client_messages import AssistantMessage
from ravi.kernel.llm.base_client import BaseModelClient
from ravi.kernel.runtime import AgentId, AgentRuntime
from ravi.kernel.tools.base_tool import BaseTool
from ravi.shared.execution import create_assistant_agent, load_session_memory

from ravi.server.services import (
    create_step,
    load_messages_for_memory,
)

logger = setup_logging()


async def load_agent_for_thread(
    db: AsyncSession,
    thread_id: uuid.UUID,
    *,
    model_client: BaseModelClient,
    tools: List[BaseTool],
    system_instructions: str,
    redis_memory: Optional[RedisMemory] = None,
    model_context_window: int = 40,
    max_iterations: int = 30,
    verbose: bool = True,
    tool_approval_handler: Optional[ToolApprovalHandler] = None,
    tools_requiring_approval: Optional[List[str]] = None,
    tool_timeout: Optional[float] = None,
    max_input_tokens: int = 16_000,
    runtime: AgentRuntime,
    enable_capability_search: bool = True,
) -> AssistantAgent:
    """Load a per-session agent whose history comes from Redis (hot) or Postgres (cold).

    Stateless design — a fresh ``ReActAgent`` is created on every request.
    The agent's memory is a per-request ``RedisMemory`` instance that shares
    the connection pool from ``app.state.redis_memory``.  Every
    ``add_message`` during the run writes through to Redis in real-time
    (fire-and-forget background task), eliminating the need for post-run sync.

    Windowing is delegated to ``SlidingWindowContext`` — the agent's memory
    stores the full history while the LLM only sees the last
    ``model_context_window`` messages per turn.

    Args:
        db:                   DB session (used only for the Postgres cold path).
        thread_id:            Thread / session identifier.
        redis_memory:         Shared ``RedisMemory`` instance from ``app.state``.
                              When ``None``, falls back to Postgres-only mode
                              with ``UnboundedMemory``.
        model_context_window: Max non-system messages passed to the LLM per
                              turn via ``SlidingWindowContext``.
        …                     All other kwargs forwarded to the shared agent factory.

    Returns:
        A configured ``AssistantAgent`` ready for ``run_stream()`` (server compat)
        or actor-model dispatch via ``on_message()``.
    """
    session_id = str(thread_id)
    context: ModelContext = SlidingWindowContext(max_messages=model_context_window)
    memory = await load_session_memory(
        session_id=session_id,
        system_instructions=system_instructions,
        redis_memory=redis_memory,
        include_mcp_app_context=True,
        cold_store_name="Postgres",
        load_persisted_steps=lambda: load_messages_for_memory(db, thread_id),
    )

    if runtime is None:
        raise ValueError(
            "load_agent_for_thread() requires a runtime. "
            "Pass app.state.runtime from the server lifespan."
        )
    agent = create_assistant_agent(
        runtime=runtime,
        model_client=model_client,
        tools=tools,
        system_instructions=system_instructions,
        memory=memory,
        model_context=context,
        max_iterations=max_iterations,
        verbose=verbose,
        tool_approval_handler=tool_approval_handler,
        tools_requiring_approval=tools_requiring_approval,
        tool_timeout=tool_timeout,
        max_input_tokens=max_input_tokens,
        agent_key=session_id,
        enable_capability_search=enable_capability_search,
    )
    return agent


async def persist_user_message(
    db: AsyncSession,
    thread_id: uuid.UUID,
    content: str,
    *,
    metadata: Optional[Dict[str, Any]] = None,
) -> uuid.UUID:
    """Save a user message step and return its ID."""
    step = await create_step(
        db,
        thread_id=thread_id,
        type="user_message",
        name="user",
        input=content,
        metadata=metadata,
    )
    return step.id


async def persist_assistant_message(
    db: AsyncSession,
    thread_id: uuid.UUID,
    message: AssistantMessage,
    *,
    parent_id: Optional[uuid.UUID] = None,
    tool_meta_map: Optional[Dict[str, Dict]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> uuid.UUID:
    """Save an assistant message step and return its ID.

    Args:
        tool_meta_map: Optional mapping of tool_name → _meta dict.
            When provided, each tool_call is enriched with _meta so the
            frontend can restore MCP App iframes when loading history.
    """
    # Serialize tool calls for storage
    generation: Dict[str, Any] = {
        "finish_reason": message.finish_reason,
    }
    if message.usage:
        generation["usage"] = {
            "prompt_tokens": message.usage.prompt_tokens,
            "completion_tokens": message.usage.completion_tokens,
            "total_tokens": message.usage.total_tokens,
        }
    if message.tool_calls:
        serialized_tcs = []
        for tc in message.tool_calls:
            tc_data = tc.model_dump(mode="json")
            # Enrich with _meta UI info for MCP App restoration
            if tool_meta_map and tc.name in tool_meta_map:
                meta = tool_meta_map[tc.name]
                ui_info = meta.get("ui", {})
                resource_uri = ui_info.get("resourceUri", "")
                if resource_uri:
                    from ravi.server.routes.mcp_apps import resolve_ui_uri

                    http_url = resolve_ui_uri(resource_uri) or resource_uri
                    tc_data["_meta"] = {
                        "ui": {
                            "resourceUri": resource_uri,
                            "httpUrl": http_url,
                        }
                    }
            serialized_tcs.append(tc_data)
        generation["tool_calls"] = serialized_tcs

    output_text = None
    if message.content:
        # Extract text from multimodal content list
        texts = [c for c in message.content if isinstance(c, str)]
        output_text = "\n".join(texts) if texts else None

    step = await create_step(
        db,
        thread_id=thread_id,
        type="assistant_message",
        name="assistant",
        output=output_text,
        generation=generation,
        parent_id=parent_id,
        metadata=metadata,
    )
    return step.id


async def persist_tool_result(
    db: AsyncSession,
    thread_id: uuid.UUID,
    tool_call_id: str,
    tool_name: str,
    output: str,
    is_error: bool = False,
    *,
    parent_id: Optional[uuid.UUID] = None,
) -> uuid.UUID:
    """Save a tool result step and return its ID."""
    step = await create_step(
        db,
        thread_id=thread_id,
        type="tool_result",
        name=tool_name,
        output=output,
        is_error=is_error,
        metadata={"tool_call_id": tool_call_id},
        parent_id=parent_id,
    )
    return step.id
