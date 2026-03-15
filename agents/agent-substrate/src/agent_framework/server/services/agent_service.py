"""Agent service – creates agents with restored per-session memory.

Responsibilities:
  1. Build agent memory from persisted steps (Redis hot path, Postgres cold path)
  2. Create configured ReActAgent per thread
  3. Sync new messages back to Redis after each agent run
  4. Persist new messages to database (Postgres cold store) during streaming

Stateless agent design
──────────────────────
Agents hold **no** state between requests.  Every request:
  1. Loads the full chat history for the thread from Redis (O(1) fast).
  2. Passes a **windowed** context (system + last N messages) to the LLM —
     this is the "selective model context" the user controls via
     ``MODEL_CONTEXT_WINDOW`` in settings.
  3. Runs the agent (messages accumulate in local ``UnboundedMemory``).
  4. Syncs the new messages back to Redis so the next request can read them.

Redis is the source of truth for active sessions.  On the first request for
a thread (cache miss), the Postgres cold store is read to seed Redis.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from agent_framework.core.agents.react_agent import ReActAgent
from agent_framework.core.guardrails.prebuilt import MaxTokenGuardrail
from agent_framework.extensions.tools.human_input import ToolApprovalHandler
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

if TYPE_CHECKING:
    from agent_framework.core.memory.redis_memory import RedisMemory

logger = logging.getLogger(__name__)


def _rebuild_memory(
    step_rows: List[Dict[str, Any]],
    system_instructions: str,
) -> UnboundedMemory:
    """Rebuild UnboundedMemory from persisted step rows.

    Maps each step type back to the proper framework message object.
    """
    memory = UnboundedMemory()

    # Always start with system message
    memory.add_message(SystemMessage(content=system_instructions))

    for row in step_rows:
        step_type = row["type"]
        meta = row.get("metadata") or {}

        if step_type == "system_message":
            # Skip – we already added the system message above
            continue

        elif step_type == "user_message":
            content_text = row.get("input") or ""
            memory.add_message(UserMessage(content=[content_text]))

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

            memory.add_message(AssistantMessage(
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
            memory.add_message(ToolExecutionResultMessage(
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
            memory.add_message(UserMessage(content=[context_msg]))

    return memory


def _build_windowed_memory(
    all_messages: List[BaseClientMessage],
    context_window: int,
) -> Tuple[UnboundedMemory, int]:
    """Build an UnboundedMemory from a windowed slice of messages.

    The system message (if present) is always preserved as the first entry.
    Only the last ``context_window`` non-system messages are included so that
    the LLM receives a bounded context while the full history stays in Redis.

    Returns:
        (memory, snapshot_count) where snapshot_count is the number of messages
        loaded into the in-process memory.  Used by callers to compute which
        messages are *new* after the agent run completes.
    """
    system_msgs = [m for m in all_messages if getattr(m, "role", None) == "system"]
    non_system = [m for m in all_messages if getattr(m, "role", None) != "system"]

    # Cap the non-system history to the context window
    windowed_non_system = non_system[-context_window:] if context_window > 0 else non_system
    windowed = system_msgs + windowed_non_system

    memory = UnboundedMemory()
    for msg in windowed:
        memory.add_message(msg)

    return memory, len(windowed)


def create_agent_for_thread(
    *,
    model_client: BaseModelClient,
    tools: List[BaseTool],
    system_instructions: str,
    memory: UnboundedMemory,
    max_iterations: int = 30,
    verbose: bool = True,
    tool_approval_handler: Optional[ToolApprovalHandler] = None,
    tools_requiring_approval: Optional[List[str]] = None,
    tool_timeout: Optional[float] = None,
    max_input_tokens: int = 16_000,
) -> ReActAgent:
    """Create a ReActAgent with pre-loaded per-session memory.

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
    redis_memory: Optional["RedisMemory"] = None,
    model_context_window: int = 40,
    max_iterations: int = 30,
    verbose: bool = True,
    tool_approval_handler: Optional[ToolApprovalHandler] = None,
    tools_requiring_approval: Optional[List[str]] = None,
    tool_timeout: Optional[float] = None,
    max_input_tokens: int = 16_000,
) -> Tuple[ReActAgent, int]:
    """Load a per-session agent whose history comes from Redis (hot) or Postgres (cold).

    Stateless design — a fresh ``ReActAgent`` is created on every request with
    a windowed snapshot of the conversation loaded from Redis.  After the run,
    callers use ``sync_new_messages_to_redis`` to push new messages back.

    Args:
        db:                   DB session (used only for the Postgres cold path).
        thread_id:            Thread / session identifier.
        redis_memory:         Shared ``RedisMemory`` instance from ``app.state``.
                              When ``None``, falls back to Postgres-only mode.
        model_context_window: Max non-system messages passed to the LLM per
                              turn (the "selective model context").  Full history
                              is preserved in Redis; only this window is loaded
                              into the agent's in-process memory.
        …                     All other kwargs forwarded to ``create_agent_for_thread``.

    Returns:
        ``(agent, snapshot_count)`` — snapshot_count is the size of the
        in-process memory at creation time.  New messages added during the run
        are ``agent.memory.get_messages()[snapshot_count:]``.
    """
    session_id = str(thread_id)
    memory: UnboundedMemory
    snapshot_count: int

    if redis_memory is not None:
        in_redis = await redis_memory.exists(session_id)

        if in_redis:
            # ── Hot path: load windowed context directly from Redis ──────────
            all_messages = await redis_memory.fetch(session_id)
            memory, snapshot_count = _build_windowed_memory(all_messages, model_context_window)
            logger.debug(
                "Redis hit for session %s — %d total, %d in window",
                session_id, len(all_messages), snapshot_count,
            )
        else:
            # ── Cold path: load from Postgres, seed Redis ────────────────────
            logger.debug("Redis miss for session %s — seeding from Postgres", session_id)
            step_rows = await load_messages_for_memory(db, thread_id)
            full_memory = _rebuild_memory(step_rows, system_instructions)
            all_messages = await full_memory.get_messages()

            # Seed Redis with the full Postgres history so future requests are fast
            if all_messages:
                await redis_memory.store_many(session_id, all_messages)
                logger.debug(
                    "Seeded Redis session %s with %d messages from Postgres",
                    session_id, len(all_messages),
                )

            # Provide the agent with a windowed context
            memory, snapshot_count = _build_windowed_memory(all_messages, model_context_window)
    else:
        # ── Fallback: Postgres only (no Redis configured) ────────────────────
        step_rows = await load_messages_for_memory(db, thread_id)
        memory = _rebuild_memory(step_rows, system_instructions)
        snapshot_count = len(await memory.get_messages())

    agent = create_agent_for_thread(
        model_client=model_client,
        tools=tools,
        system_instructions=system_instructions,
        memory=memory,
        max_iterations=max_iterations,
        verbose=verbose,
        tool_approval_handler=tool_approval_handler,
        tools_requiring_approval=tools_requiring_approval,
        tool_timeout=tool_timeout,
        max_input_tokens=max_input_tokens,
    )
    return agent, snapshot_count


async def sync_new_messages_to_redis(
    redis_memory: "RedisMemory",
    session_id: str,
    agent: ReActAgent,
    snapshot_count: int,
) -> None:
    """Write messages added during the agent run back to Redis.

    After ``agent.run_stream()`` completes, the agent's in-process memory
    contains all pre-loaded messages PLUS the new ones from this turn.
    This function extracts the new messages and appends them to Redis so the
    next request can pick them up without touching Postgres.

    Args:
        redis_memory:   Shared ``RedisMemory`` instance.
        session_id:     Thread ID as string (Redis key namespace).
        agent:          The agent whose memory holds the completed run.
        snapshot_count: Value returned by ``load_agent_for_thread`` —
                        messages at or above this index are new.
    """
    all_messages = await agent.memory.get_messages()
    new_messages = all_messages[snapshot_count:]
    if not new_messages:
        return
    try:
        await redis_memory.store_many(session_id, new_messages)
        logger.debug(
            "Synced %d new message(s) to Redis session %s",
            len(new_messages), session_id,
        )
    except Exception:
        logger.exception(
            "Failed to sync new messages to Redis for session %s — "
            "history will be rebuilt from Postgres on the next request",
            session_id,
        )


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
