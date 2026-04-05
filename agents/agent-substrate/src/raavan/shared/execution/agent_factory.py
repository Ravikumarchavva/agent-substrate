"""Shared agent factory for monolith and distributed execution paths."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Dict, List, Optional

from raavan.catalog.tools.human_input.tool import ToolApprovalHandler
from raavan.core.agents.react_agent import ReActAgent
from raavan.core.context.base_context import ModelContext
from raavan.core.context.implementations import SlidingWindowContext
from raavan.core.execution.context import ExecutionContext
from raavan.core.guardrails.prebuilt import MaxTokenGuardrail
from raavan.core.llm.base_client import BaseModelClient
from raavan.core.memory.base_memory import BaseMemory
from raavan.core.memory.unbounded_memory import UnboundedMemory
from raavan.core.messages.base_message import BaseClientMessage
from raavan.core.messages.client_messages import (
    AssistantMessage,
    SystemMessage,
    ToolCallMessage,
    ToolExecutionResultMessage,
    UserMessage,
)
from raavan.core.runtime import AgentId, AgentRuntime
from raavan.core.tools.base_tool import BaseTool
from raavan.integrations.memory.redis_memory import RedisMemory

logger = logging.getLogger(__name__)

PersistedStepLoader = Callable[[], Awaitable[List[Dict[str, Any]]]]


async def rebuild_messages_from_steps(
    step_rows: List[Dict[str, Any]],
    system_instructions: str,
    *,
    include_mcp_app_context: bool = False,
) -> List[BaseClientMessage]:
    """Rebuild framework messages from persisted step rows."""
    messages: List[BaseClientMessage] = [SystemMessage(content=system_instructions)]

    for row in step_rows:
        step_type = row["type"]
        meta = row.get("metadata") or {}

        if step_type == "system_message":
            continue

        if step_type == "user_message":
            messages.append(UserMessage(content=[row.get("input") or ""]))
            continue

        if step_type == "assistant_message":
            output_text = row.get("output")
            content = [output_text] if output_text else None

            tool_calls = None
            generation = row.get("generation") or {}
            if generation.get("tool_calls"):
                tool_calls = [
                    ToolCallMessage(**tool_call)
                    for tool_call in generation["tool_calls"]
                ]

            messages.append(
                AssistantMessage(
                    content=content,
                    tool_calls=tool_calls,
                    finish_reason=generation.get("finish_reason", "stop"),
                )
            )
            continue

        if step_type == "tool_result":
            messages.append(
                ToolExecutionResultMessage(
                    tool_call_id=meta.get("tool_call_id", ""),
                    name=row.get("name", ""),
                    content=[{"type": "text", "text": row.get("output") or ""}],
                    is_error=row.get("is_error") or False,
                )
            )
            continue

        if step_type == "tool_call":
            continue

        if step_type == "mcp_app_context" and include_mcp_app_context:
            tool_name = row.get("name", "mcp_app")
            context_data = row.get("output") or ""
            context_msg = (
                f"[MCP App Update — {tool_name}] "
                f"The user interacted with the {tool_name} widget. "
                f"Current state:\n{context_data}"
            )
            messages.append(UserMessage(content=[context_msg]))

    return messages


async def load_session_memory(
    *,
    session_id: str,
    system_instructions: str,
    load_persisted_steps: PersistedStepLoader,
    redis_memory: Optional[RedisMemory] = None,
    include_mcp_app_context: bool = False,
    cold_store_name: str = "persisted store",
) -> BaseMemory:
    """Load session memory from Redis hot store or a persisted cold store."""
    if redis_memory is not None:
        per_request_mem = RedisMemory.for_session(redis_memory, session_id)
        in_redis = await redis_memory.exists(session_id)

        if in_redis:
            count = await per_request_mem.restore()
            logger.debug(
                "Redis hit for %s — %d messages restored",
                session_id,
                count,
            )
            return per_request_mem

        logger.debug(
            "Redis miss for %s — loading from %s",
            session_id,
            cold_store_name,
        )
        step_rows = await load_persisted_steps()
        all_messages = await rebuild_messages_from_steps(
            step_rows,
            system_instructions,
            include_mcp_app_context=include_mcp_app_context,
        )

        if all_messages:
            await redis_memory.store_many(session_id, all_messages)
            logger.debug(
                "Seeded Redis session %s with %d messages from %s",
                session_id,
                len(all_messages),
                cold_store_name,
            )

        await per_request_mem.restore()
        return per_request_mem

    step_rows = await load_persisted_steps()
    all_messages = await rebuild_messages_from_steps(
        step_rows,
        system_instructions,
        include_mcp_app_context=include_mcp_app_context,
    )
    fallback_mem = UnboundedMemory()
    for message in all_messages:
        await fallback_mem.add_message(message)
    return fallback_mem


def create_react_agent(
    *,
    model_client: BaseModelClient,
    tools: List[BaseTool],
    system_instructions: str,
    memory: BaseMemory,
    model_context: Optional[ModelContext] = None,
    model_context_window: int = 40,
    max_iterations: int = 30,
    verbose: bool = True,
    tool_approval_handler: Optional[ToolApprovalHandler] = None,
    tools_requiring_approval: Optional[List[str]] = None,
    tool_timeout: Optional[float] = None,
    max_input_tokens: int = 16_000,
    runtime: Optional[AgentRuntime] = None,
    agent_id: Optional[AgentId] = None,
    execution_context: Optional[ExecutionContext] = None,
) -> ReActAgent:
    """Create a configured ``ReActAgent`` with shared defaults."""
    resolved_context = model_context or SlidingWindowContext(
        max_messages=model_context_window
    )

    kwargs: Dict[str, Any] = dict(
        name="ChatBot",
        description="A helpful AI assistant with tool access.",
        model_client=model_client,
        model_context=resolved_context,
        tools=tools,
        system_instructions=system_instructions,
        memory=memory,
        max_iterations=max_iterations,
        verbose=verbose,
        input_guardrails=[
            MaxTokenGuardrail(
                max_tokens=max_input_tokens,
                model="gpt-4o",
                tripwire=True,
            )
        ],
    )
    if tool_approval_handler is not None:
        kwargs["tool_approval_handler"] = tool_approval_handler
    if tools_requiring_approval is not None:
        kwargs["tools_requiring_approval"] = tools_requiring_approval
    if tool_timeout is not None:
        kwargs["tool_timeout"] = tool_timeout
    if runtime is not None:
        kwargs["runtime"] = runtime
    if agent_id is not None:
        kwargs["agent_id"] = agent_id
    if execution_context is not None:
        kwargs["execution_context"] = execution_context
    return ReActAgent(**kwargs)
