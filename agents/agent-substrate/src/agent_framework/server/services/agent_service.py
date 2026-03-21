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

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from agent_framework.core.agents.react_agent import ReActAgent
from agent_framework.core.context.base_context import ModelContext
from agent_framework.core.context.implementations import SlidingWindowContext
from agent_framework.core.guardrails.prebuilt import MaxTokenGuardrail
from agent_framework.extensions.tools.human_input import ToolApprovalHandler
from agent_framework.core.memory.base_memory import BaseMemory
from agent_framework.core.memory.redis_memory import RedisMemory
from agent_framework.core.memory.unbounded_memory import UnboundedMemory
from agent_framework.core.messages.client_messages import (
    AssistantMessage,
    SystemMessage,
    ToolCallMessage,
    ToolExecutionResultMessage,
    UserMessage,
)
from agent_framework.core.messages.base_message import BaseClientMessage
from agent_framework.providers.llm.base_client import BaseModelClient
from agent_framework.core.tools.base_tool import BaseTool

from agent_framework.server.services import (
    create_step,
    load_messages_for_memory,
)

logger = logging.getLogger(__name__)


async def _rebuild_messages(
    step_rows: List[Dict[str, Any]],
    system_instructions: str,
) -> List[BaseClientMessage]:
    """Rebuild a message list from persisted Postgres step rows.

    Maps each step type back to the proper framework message object.
    Used only on the cold path (Redis miss) to seed Redis with the
    full Postgres history.

    Returns:
        Ordered list of ``BaseClientMessage`` starting with the system prompt.
    """
    messages: List[BaseClientMessage] = []

    # Always start with system message
    messages.append(SystemMessage(content=system_instructions))

    for row in step_rows:
        step_type = row["type"]
        meta = row.get("metadata") or {}

        if step_type == "system_message":
            # Skip – we already added the system message above
            continue

        elif step_type == "user_message":
            content_text = row.get("input") or ""
            messages.append(UserMessage(content=[content_text]))

        elif step_type == "assistant_message":
            output_text = row.get("output")
            content = [output_text] if output_text else None
            
            # Rebuild tool calls if stored
            tool_calls = None
            gen = row.get("generation") or {}
            if gen.get("tool_calls"):
                tool_calls = [
                    ToolCallMessage(**tc) for tc in gen["tool_calls"]
                ]

            messages.append(AssistantMessage(
                content=content,
                tool_calls=tool_calls,
                finish_reason=gen.get("finish_reason", "stop"),
            ))

        elif step_type == "tool_call":
            # Tool calls are embedded in assistant message, skip standalone
            pass

        elif step_type == "tool_result":
            tool_call_id = meta.get("tool_call_id", "")
            tool_name = row.get("name", "")
            output = row.get("output") or ""
            is_error = row.get("is_error") or False
            messages.append(ToolExecutionResultMessage(
                tool_call_id=tool_call_id,
                name=tool_name,
                content=[{"type": "text", "text": output}],
                isError=is_error,
            ))

        elif step_type == "mcp_app_context":
            # MCP App context update — inject as a user message so the LLM
            # is aware of user interactions within interactive widgets.
            tool_name = row.get("name", "mcp_app")
            context_data = row.get("output") or ""
            context_msg = (
                f"[MCP App Update — {tool_name}] "
                f"The user interacted with the {tool_name} widget. "
                f"Current state:\n{context_data}"
            )
            messages.append(UserMessage(content=[context_msg]))

    return messages


def create_agent_for_thread(
    *,
    model_client: BaseModelClient,
    tools: List[BaseTool],
    system_instructions: str,
    memory: BaseMemory,
    model_context: ModelContext,
    max_iterations: int = 30,
    verbose: bool = True,
    tool_approval_handler: Optional[ToolApprovalHandler] = None,
    tools_requiring_approval: Optional[List[str]] = None,
    tool_timeout: Optional[float] = None,
    max_input_tokens: int = 16_000,
) -> ReActAgent:
    """Create a ReActAgent with pre-loaded per-session memory.

    Args:
        memory:        Per-request memory (``RedisMemory`` or ``UnboundedMemory``).
        model_context: Windowing strategy that filters messages before each LLM
                       call.  Typically ``SlidingWindowContext(max_messages=N)``.

    A ``MaxTokenGuardrail`` is always installed as a default input guardrail
    to prevent runaway context costs and prompt injection via oversized inputs.
    The limit can be tuned via ``max_input_tokens`` (default: 16 000 tokens).
    """
    # Default input guardrail — accurate token counting via tiktoken
    default_input_guardrails = [
        MaxTokenGuardrail(
            max_tokens=max_input_tokens,
            model="gpt-4o",
            tripwire=True,
        )
    ]

    kwargs: Dict[str, Any] = dict(
        name="ChatBot",
        description="A helpful AI assistant with tool access.",
        model_client=model_client,
        model_context=model_context,
        tools=tools,
        system_instructions=system_instructions,
        memory=memory,
        max_iterations=max_iterations,
        verbose=verbose,
        input_guardrails=default_input_guardrails,
    )
    if tool_approval_handler is not None:
        kwargs["tool_approval_handler"] = tool_approval_handler
    if tools_requiring_approval is not None:
        kwargs["tools_requiring_approval"] = tools_requiring_approval
    if tool_timeout is not None:
        kwargs["tool_timeout"] = tool_timeout
    return ReActAgent(**kwargs)


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
) -> ReActAgent:
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
        …                     All other kwargs forwarded to ``create_agent_for_thread``.

    Returns:
        A configured ``ReActAgent`` ready for ``run_stream()``.
    """
    session_id = str(thread_id)
    memory: BaseMemory
    context: ModelContext = SlidingWindowContext(max_messages=model_context_window)

    if redis_memory is not None:
        # Create a per-request RedisMemory bound to this session, sharing
        # the parent's connection pool (no new TCP connections).
        per_request_mem = RedisMemory.for_session(redis_memory, session_id)

        in_redis = await redis_memory.exists(session_id)

        if in_redis:
            # ── Hot path: restore ALL messages from Redis ────────────────────
            # Redis holds the full thread history (capped at max_messages).
            # SlidingWindowContext is responsible for selecting the last
            # model_context_window messages to send to the LLM — that
            # filtering happens at LLM-call time, not here.
            count = await per_request_mem.restore()
            logger.debug(
                "Redis hit for session %s — %d messages restored",
                session_id, count,
            )
        else:
            # ── Cold path: rebuild from Postgres, seed Redis, then restore ───
            logger.debug("Redis miss for session %s — seeding from Postgres", session_id)
            step_rows = await load_messages_for_memory(db, thread_id)
            all_messages = await _rebuild_messages(step_rows, system_instructions)

            # Seed Redis with the full Postgres history so future requests are fast
            if all_messages:
                await redis_memory.store_many(session_id, all_messages)
                logger.debug(
                    "Seeded Redis session %s with %d messages from Postgres",
                    session_id, len(all_messages),
                )

            # Restore into the per-request memory (loads from Redis)
            await per_request_mem.restore()

        memory = per_request_mem
    else:
        # ── Fallback: Postgres only (no Redis configured) ────────────────
        step_rows = await load_messages_for_memory(db, thread_id)
        all_messages = await _rebuild_messages(step_rows, system_instructions)
        fallback_mem = UnboundedMemory()
        for msg in all_messages:
            await fallback_mem.add_message(msg)
        memory = fallback_mem

    agent = create_agent_for_thread(
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
    )
    return agent


async def persist_user_message(
    db: AsyncSession,
    thread_id: uuid.UUID,
    content: str,
) -> uuid.UUID:
    """Save a user message step and return its ID."""
    step = await create_step(
        db,
        thread_id=thread_id,
        type="user_message",
        name="user",
        input=content,
    )
    return step.id


async def persist_assistant_message(
    db: AsyncSession,
    thread_id: uuid.UUID,
    message: AssistantMessage,
    *,
    parent_id: Optional[uuid.UUID] = None,
    tool_meta_map: Optional[Dict[str, Dict]] = None,
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
            tc_data = tc.to_dict()
            # Enrich with _meta UI info for MCP App restoration
            if tool_meta_map and tc.name in tool_meta_map:
                meta = tool_meta_map[tc.name]
                ui_info = meta.get("ui", {})
                resource_uri = ui_info.get("resourceUri", "")
                if resource_uri:
                    from agent_framework.server.routes.mcp_apps import resolve_ui_uri
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
