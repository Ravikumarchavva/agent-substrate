"""Agent service – creates agents backed by a shared HistoryProvider.

Responsibilities:
  1. Ensure the session is populated in the shared ``HistoryProvider``
     (cache hit; on a miss, seed it from the Postgres cold store).
  2. Create a configured ``AssistantAgent`` per thread, bound to its session_id.
  3. Persist new messages to the database (cold store) during streaming.

Stateless agent design
──────────────────────
Agents hold **no** state between requests.  Every request:
  1. Reuses the shared, multi-session ``HistoryProvider`` from ``app.state``;
     the agent addresses it by ``session_id`` (the thread id).
  2. On a cache miss, seeds the session from the Postgres cold store.
  3. Passes a ``SlidingWindowContext(max_messages=N)`` to the agent — the LLM
     only sees the last N messages, while the full history stays in the
     provider.
  4. Runs the agent — each ``save_messages()`` writes through to the provider.

The provider is the source of truth for active sessions.  On the first request
for a thread (cache miss), the Postgres cold store is read to seed it.
"""

from __future__ import annotations
from ravi.logger import setup_logging

import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ravi.agents.assistant import AssistantAgent
from ravi.agents.context import (
    HistoryProvider,
    SlidingWindowCompaction,
)
from ravi.capabilities.tools.human_input.tool import ToolApprovalHandler
from ravi.kernel.llm import LLMClient as BaseModelClient
from ravi.kernel import ChatMessage, TextBlock, ToolUseBlock, AgentRuntime, Tool
from ravi.serving.shared.execution import create_assistant_agent, load_session_memory

from ravi.serving.monolith.services import (
    create_step,
    load_messages_for_memory,
)

logger = setup_logging()


async def load_agent_for_thread(
    db: AsyncSession,
    thread_id: uuid.UUID,
    *,
    model_client: BaseModelClient,
    tools: List[Tool],
    system_instructions: str,
    history: Optional[HistoryProvider] = None,
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
    """Load a per-session agent backed by the shared ``HistoryProvider``.

    Stateless design — a fresh ``AssistantAgent`` is created on every request.
    The agent shares the multi-session ``HistoryProvider`` from
    ``app.state.history`` and addresses it by ``session_id`` (the thread id).
    Every ``save_messages`` during the run writes through to that provider.

    Windowing is delegated to ``SlidingWindowContext`` — the provider stores
    the full history while the LLM only sees the last
    ``model_context_window`` messages per turn.

    Args:
        db:                   DB session (used only for the Postgres cold path).
        thread_id:            Thread / session identifier.
        history:              Shared ``HistoryProvider`` from ``app.state``.
                              When ``None``, falls back to an in-process
                              ``InMemoryHistoryProvider`` seeded from Postgres.
        model_context_window: Max non-system messages passed to the LLM per
                              turn via ``SlidingWindowContext``.
        …                     All other kwargs forwarded to the shared agent factory.

    Returns:
        A configured ``AssistantAgent`` ready for ``run_stream()`` (server compat)
        or actor-model dispatch via ``on_message()``.
    """
    session_id = str(thread_id)
    memory = await load_session_memory(
        session_id=session_id,
        system_instructions=system_instructions,
        history=history,
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
        model_context=SlidingWindowCompaction(max_messages=model_context_window),
        max_iterations=max_iterations,
        tool_timeout=tool_timeout,
        # legacy kwargs forwarded and dropped by factory:
        verbose=verbose,
        tool_approval_handler=tool_approval_handler,
        tools_requiring_approval=tools_requiring_approval,
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
    message: ChatMessage | Any,
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
        "finish_reason": getattr(message, "finish_reason", "stop"),
    }
    usage = getattr(message, "usage", None)
    if usage:
        generation["usage"] = {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0),
            "completion_tokens": getattr(usage, "completion_tokens", 0),
            "total_tokens": getattr(usage, "total_tokens", 0),
        }

    tool_calls = getattr(message, "tool_calls", None)
    if not tool_calls and hasattr(message, "content") and isinstance(message.content, list):
        tool_calls = [
            block for block in message.content if isinstance(block, ToolUseBlock)
        ]

    if tool_calls:
        serialized_tcs = []
        for tc in tool_calls:
            tc_name = getattr(tc, "name", getattr(tc, "tool_name", "unknown"))
            tc_data = tc.model_dump(mode="json") if hasattr(tc, "model_dump") else {}
            # Enrich with _meta UI info for MCP App restoration
            if tool_meta_map and tc_name in tool_meta_map:
                meta = tool_meta_map[tc_name]
                ui_info = meta.get("ui", {})
                resource_uri = ui_info.get("resourceUri", "")
                if resource_uri:
                    from ravi.serving.monolith.routes.mcp_apps import resolve_ui_uri

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
    if hasattr(message, "content") and message.content:
        # Extract text from multimodal content list or blocks
        texts = []
        for c in message.content:
            if isinstance(c, TextBlock):
                texts.append(c.text)
            elif isinstance(c, str):
                texts.append(c)
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
