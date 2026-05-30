"""Shared agent factory for monolith and distributed execution paths."""

from __future__ import annotations
from ravi.logger import setup_logging

from collections.abc import Awaitable, Callable
from typing import Any, Dict, List, Optional

from ravi.fabric.context import (
    HistoryProvider,
    InMemoryHistoryProvider,
    SlidingWindowCompaction as SlidingWindowStrategy,
)
from ravi.fabric.llm import LLMClient as BaseModelClient
from ravi.kernel import (
    ChatMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    AgentRuntime,
    Tool,
)

logger = setup_logging()


class GuardrailSpec:
    def __init__(self, input=None, output=None, tool_call=None):
        self.input = input or []
        self.output = output or []
        self.tool_call = tool_call or []

    def is_empty(self) -> bool:
        return not (self.input or self.output or self.tool_call)


PersistedStepLoader = Callable[[], Awaitable[List[Dict[str, Any]]]]


async def rebuild_messages_from_steps(
    step_rows: List[Dict[str, Any]],
    system_instructions: str,
    *,
    include_mcp_app_context: bool = False,
) -> List[ChatMessage]:
    """Rebuild framework messages from persisted step rows using unified ChatMessage."""
    messages: List[ChatMessage] = []
    if system_instructions:
        messages.append(
            ChatMessage(
                role="system",
                content=[TextBlock(text=system_instructions)],
            )
        )

    for row in step_rows:
        step_type = row["type"]
        meta = row.get("metadata") or {}

        if step_type == "system_message":
            continue

        if step_type == "user_message":
            messages.append(
                ChatMessage(
                    role="user",
                    content=[TextBlock(text=row.get("input") or "")],
                )
            )
            continue

        if step_type == "assistant_message":
            content_blocks = []
            output_text = row.get("output")
            if output_text:
                content_blocks.append(TextBlock(text=output_text))

            generation = row.get("generation") or {}
            if generation.get("tool_calls"):
                for tool_call in generation["tool_calls"]:
                    call_id = tool_call.get("id") or tool_call.get("call_id") or ""
                    tool_name = tool_call.get("name") or tool_call.get("tool_name") or ""
                    args = tool_call.get("arguments") or {}
                    content_blocks.append(
                        ToolUseBlock(
                            call_id=call_id,
                            tool_name=tool_name,
                            arguments=args,
                        )
                    )

            messages.append(
                ChatMessage(
                    role="assistant",
                    content=content_blocks,
                )
            )
            continue

        if step_type == "tool_result":
            call_id = meta.get("tool_call_id") or ""
            tool_name = row.get("name") or ""
            output_text = row.get("output") or ""
            is_error = row.get("is_error") or False
            messages.append(
                ChatMessage(
                    role="tool",
                    content=[
                        ToolResultBlock(
                            call_id=call_id,
                            tool_name=tool_name,
                            content=[TextBlock(text=output_text)],
                            is_error=is_error,
                        )
                    ],
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
            messages.append(
                ChatMessage(
                    role="user",
                    content=[TextBlock(text=context_msg)],
                )
            )

    return messages


async def load_session_memory(
    *,
    session_id: str,
    system_instructions: str,
    load_persisted_steps: PersistedStepLoader,
    history: Optional[HistoryProvider] = None,
    include_mcp_app_context: bool = False,
    cold_store_name: str = "persisted store",
) -> HistoryProvider:
    """Ensure *session_id* is populated in a history provider and return it.

    When *history* is provided (the shared, multi-session provider), a cold
    session is seeded from the persisted cold store on a cache miss.  When it
    is ``None``, a fresh in-process provider is returned seeded from the cold
    store.
    """
    if history is not None:
        if await history.count_messages(session_id) > 0:
            logger.debug("History hit for %s", session_id)
            return history

        logger.debug(
            "History miss for %s — loading from %s", session_id, cold_store_name
        )
        step_rows = await load_persisted_steps()
        all_messages = await rebuild_messages_from_steps(
            step_rows,
            system_instructions,
            include_mcp_app_context=include_mcp_app_context,
        )
        if all_messages:
            await history.save_messages(session_id, all_messages)
            logger.debug(
                "Seeded session %s with %d messages from %s",
                session_id,
                len(all_messages),
                cold_store_name,
            )
        return history

    step_rows = await load_persisted_steps()
    all_messages = await rebuild_messages_from_steps(
        step_rows,
        system_instructions,
        include_mcp_app_context=include_mcp_app_context,
    )
    fallback = InMemoryHistoryProvider()
    if all_messages:
        await fallback.save_messages(session_id, all_messages)
    return fallback


def create_assistant_agent(
    *,
    runtime: AgentRuntime,
    model_client: BaseModelClient,
    tools: Optional[List[Tool]] = None,
    system_instructions: str = "",
    memory: Optional[HistoryProvider] = None,
    model_context: Optional[Any] = None,
    model_context_window: int = 40,
    max_iterations: int = 30,
    tool_timeout: Optional[float] = None,
    name: str = "ChatBot",
    # legacy kwargs accepted but unused (# TODO L4-guardrails / # TODO L4-hitl)
    **_dropped: Any,
) -> Any:
    """Create a configured ``AssistantAgent`` for the actor-model runtime.

    Maps the old parameter surface to the new ``AssistantAgent.__init__``
    signature.  Legacy kwargs (guardrails, verbose, session_id, etc.) are
    silently accepted and dropped until L4 guardrails are wired.
    """
    from ravi.reasoning.agents.assistant.agent import AssistantAgent

    # Resolve compaction: accept SlidingWindowCompaction directly or fall back
    # to a window derived from model_context_window.
    compaction: Optional[SlidingWindowStrategy] = None
    if isinstance(model_context, SlidingWindowStrategy):
        compaction = model_context
    elif model_context is None:
        compaction = SlidingWindowStrategy(max_messages=model_context_window)
    # AgentContext objects (Protocol) can't be introspected here — fall back to default

    return AssistantAgent(
        name,
        runtime,
        model=model_client,
        tools=tools,
        system=system_instructions or None,
        history=memory,
        compaction=compaction,
        max_iterations=max_iterations,
        tool_timeout=tool_timeout,
    )

