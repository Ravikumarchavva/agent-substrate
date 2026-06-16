"""Shared agent factory for monolith and distributed execution paths."""

from __future__ import annotations
from ravi.logger import setup_logging

from collections.abc import Awaitable, Callable
from typing import Any, Dict, List, Optional

from ravi.agents.context import (
    HistoryProvider,
    InMemoryHistoryProvider,
    SlidingWindowCompaction,
)
from ravi.kernel.llm import LLMClient
from ravi.kernel import (
    ChatMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    Tool,
)
from ravi.kernel.core.identity import AgentId

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
                    tool_name = (
                        tool_call.get("name") or tool_call.get("tool_name") or ""
                    )
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
    _aid = AgentId(type="assistant", key=session_id)

    async def _seed(provider: HistoryProvider) -> None:
        """Load cold-store ChatMessages into *provider* as Message envelopes."""
        step_rows = await load_persisted_steps()
        chat_messages = await rebuild_messages_from_steps(
            step_rows,
            system_instructions,
            include_mcp_app_context=include_mcp_app_context,
        )
        # Seed the session history with the actual ChatMessage turns.
        # The history provider contract stores ChatMessage objects directly,
        # so we must not wrap them in Message envelopes here.
        for chat_msg in chat_messages:
            await provider.append(
                _aid,
                chat_msg,
                session_id=session_id,
                run_id="cold_store",
            )
        if chat_messages:
            logger.debug(
                "Seeded session %s with %d messages from %s",
                session_id,
                len(chat_messages),
                cold_store_name,
            )

    if history is not None:
        # count_messages exists on RedisHistoryProvider but not on InMemoryHistoryProvider.
        # Fall back to get_messages length check when the method is absent.
        if hasattr(history, "count_messages"):
            hit = await history.count_messages(_aid, session_id=session_id) > 0  # type: ignore[attr-defined]
        else:
            hit = (
                len(await history.get_messages(_aid, session_id=session_id, limit=1))
                > 0
            )

        if hit:
            logger.debug("History hit for %s", session_id)
            return history

        logger.debug(
            "History miss for %s — loading from %s", session_id, cold_store_name
        )
        await _seed(history)
        return history

    fallback = InMemoryHistoryProvider()
    await _seed(fallback)
    return fallback


def rebuild_agent(
    spec: dict,
    *,
    model_client: LLMClient,
    tools: Optional[List[Tool]] = None,
) -> Any:
    """Reconstruct an agent from a persisted spec (used for cold resume).

    The spec is the dict saved at submit time:
    ``{mode, model, system_instructions, tool_names, max_iterations,
       session_id, model_context_window}``.

    ``tools`` should be the resolved tool objects (caller looks up by name
    from the live registry); any ``tool_names`` not found are silently dropped.
    """
    from ravi.agents.core import ReActAgent
    from ravi.agents.tools.toolbox import Toolbox
    from ravi.agents.context import ContextConfig

    session_id = spec.get("session_id", "resumed")
    max_iterations = spec.get("max_iterations", 30)
    model_context_window = spec.get("model_context_window", 40)
    system_instructions = spec.get("system_instructions", "")

    ctx = ContextConfig(
        InMemoryHistoryProvider(),
        SlidingWindowCompaction(max_messages=model_context_window),
    )

    toolbox = Toolbox()
    for t in (tools or []):
        toolbox.add(t)

    return ReActAgent(
        f"assistant-{session_id[:8]}",
        model=model_client,
        tools=toolbox if tools else None,
        system_instructions=system_instructions,
        context=ctx,
        max_iterations=max_iterations,
    )


def create_assistant_agent(
    *,
    runtime: Any,
    model_client: LLMClient,
    tools: Optional[List[Tool]] = None,
    system_instructions: str = "",
    memory: Optional[HistoryProvider] = None,
    model_context: Optional[Any] = None,
    model_context_window: int = 40,
    max_iterations: int = 30,
    tool_timeout: Optional[float] = None,
    name: str = "ChatBot",
) -> Any:
    """Create a configured ``ReActAgent`` for the runtime."""
    from ravi.agents.core import ReActAgent
    from ravi.agents.tools.toolbox import Toolbox

    # Resolve compaction: accept SlidingWindowCompaction directly or fall back
    # to a window derived from model_context_window.
    compaction: Optional[SlidingWindowCompaction] = None
    if isinstance(model_context, SlidingWindowCompaction):
        compaction = model_context
    elif model_context is None:
        compaction = SlidingWindowCompaction(max_messages=model_context_window)

    from ravi.agents.context import ContextConfig

    if memory is not None:
        ctx = ContextConfig(
            memory,
            compaction or SlidingWindowCompaction(max_messages=model_context_window),
        )
    elif compaction is not None:
        ctx = ContextConfig(InMemoryHistoryProvider(), compaction)
    else:
        ctx = ContextConfig(
            InMemoryHistoryProvider(),
            SlidingWindowCompaction(max_messages=model_context_window),
        )

    # Build tool registry
    toolbox = Toolbox()
    for t in (tools or []):
        toolbox.add(t)

    return ReActAgent(
        name,
        model=model_client,
        tools=toolbox if tools else None,
        system_instructions=system_instructions or "",
        context=ctx,
        max_iterations=max_iterations,
    )
