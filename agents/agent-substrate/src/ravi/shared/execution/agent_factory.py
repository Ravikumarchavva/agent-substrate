"""Shared agent factory for monolith and distributed execution paths."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Dict, List, Optional

from ravi.catalog.tools.human_input.tool import ToolApprovalHandler
from ravi.core.agent_catalog._catalog import AgentCatalog
from ravi.core.agents.react_agent import ReActAgent
from ravi.core.context.base_context import ModelContext
from ravi.core.context.implementations import SlidingWindowContext
from ravi.core.execution.context import ExecutionContext
from ravi.core.guardrails.prebuilt import MaxTokenGuardrail
from ravi.core.llm.base_client import BaseModelClient
from ravi.core.memory.base_memory import BaseMemory
from ravi.core.memory.unbounded_memory import UnboundedMemory
from ravi.core.messages.base_message import BaseClientMessage
from ravi.core.messages.client_messages import (
    AssistantMessage,
    SystemMessage,
    ToolCallMessage,
    ToolExecutionResultMessage,
    UserMessage,
)
from ravi.core.messages.content import TextBlock
from ravi.core.runtime import AgentId, AgentRuntime
from ravi.core.tools.base_tool import BaseTool
from ravi.integrations.memory.redis_memory import RedisMemory

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
                    content=[TextBlock(text=row.get("output") or "")],
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
    enable_capability_search: bool = True,
) -> ReActAgent:
    """Create a configured ``ReActAgent`` with shared defaults.

    Builds a catalog from the provided resources and passes it to ``ReActAgent``.
    """
    from ravi.core.middleware.builtins.guardrails import GuardrailsMiddleware

    resolved_context = model_context or SlidingWindowContext(max_messages=model_context_window)

    catalog = AgentCatalog()
    catalog.register_model("primary", model_client)
    catalog.register_context("default", resolved_context)
    catalog.register_memory("default", memory)
    for tool in tools:
        catalog.register_tool(tool)

    kwargs: Dict[str, Any] = dict(
        name="ChatBot",
        description="A helpful AI assistant with tool access.",
        catalog=catalog,
        system_instructions=system_instructions,
        max_iterations=max_iterations,
        verbose=verbose,
        middleware=[
            GuardrailsMiddleware(
                input_guardrails=[
                    MaxTokenGuardrail(
                        max_tokens=max_input_tokens,
                        model="gpt-4o",
                        tripwire=True,
                    )
                ]
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
    kwargs["enable_capability_search"] = enable_capability_search
    return ReActAgent(**kwargs)
