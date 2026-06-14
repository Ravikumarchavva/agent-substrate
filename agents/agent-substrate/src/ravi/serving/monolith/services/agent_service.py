"""Agent service — builds and registers ReActAgents, returns stream adapters.

Each chat thread maps to a ``correlation_id`` (the session/thread ID).  The
agent is registered once with the ``Runtime``; each request submits a
new inbox Message with that correlation_id.  The EventLog records every step.

The returned ``RunStreamAdapter`` presents the ``run_stream()`` interface
expected by ``AgentStreamSession``, forwarding log entries as stream events.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ravi.agents.core import ReActAgent, OrchestratorAgent, SubAgentConfig
from ravi.agents.context import (
    ContextConfig,
    HistoryProvider,
    InMemoryHistoryProvider,
    SlidingWindowCompaction,
)
from ravi.capabilities.tools.human_input import ToolApprovalHandler
from ravi.kernel.llm import LLMClient as BaseModelClient
from ravi.kernel import ChatMessage, TextBlock, ToolUseBlock, Tool
from ravi.serving.stream.run_adapter import RunStreamAdapter
from ravi.serving.shared.execution import load_session_memory
from ravi.config import settings

from ravi.serving.monolith.services import (
    create_step,
    load_messages_for_memory,
)
from ravi.logger import setup_logging

logger = setup_logging()


def _make_context(max_messages: int = 20) -> ContextConfig:
    return ContextConfig(
        InMemoryHistoryProvider(),
        SlidingWindowCompaction(max_messages=max_messages),
    )


def _build_orchestrator(
    model_client: BaseModelClient,
    runtime: Any,
    model_context_window: int,
    tool_timeout: float | None,
    session_id: str,
    tools: list[Tool] | None = None,
) -> RunStreamAdapter:
    """Build an OrchestratorAgent with researcher + calculator + clock specialists."""
    from ravi.agents.tools.toolbox import Toolbox
    from ravi.capabilities.tools import (
        CalculatorTool,
        CurrentTimeTool,
        WebSearchTool,
        ReadUrlTool,
    )

    def _registry(*tool_instances) -> Toolbox:
        tb = Toolbox()
        for t in tool_instances:
            tb.register(t)
        return tb

    researcher = ReActAgent(
        "researcher",
        model=model_client,
        tools=_registry(WebSearchTool(), ReadUrlTool()),
        context=_make_context(model_context_window),
        system_instructions="You are a research specialist. Use web_search and read_url to find accurate, up-to-date facts. Return a concise answer.",
        max_iterations=5,
    )
    calculator = ReActAgent(
        "calculator",
        model=model_client,
        tools=_registry(CalculatorTool()),
        context=_make_context(model_context_window),
        system_instructions="You are a calculation specialist. Use the calculator tool for all arithmetic. Return the result with a brief explanation.",
        max_iterations=3,
    )
    clock = ReActAgent(
        "clock",
        model=model_client,
        tools=_registry(CurrentTimeTool()),
        context=_make_context(model_context_window),
        system_instructions="You are a time specialist. Use current_time to get the exact date and time. Return it in a clear, human-readable format.",
        max_iterations=2,
    )

    orchestrator = OrchestratorAgent(
        "coordinator",
        model=model_client,
        sub_agents=[
            SubAgentConfig(researcher, description="Searches the web and reads URLs for current information.", ask_timeout=60.0),
            SubAgentConfig(calculator, description="Performs precise numerical calculations.", ask_timeout=30.0),
            SubAgentConfig(clock, description="Reports the current date and time.", ask_timeout=10.0),
        ],
        max_iterations=15,
        context=_make_context(model_context_window),
    )

    import asyncio
    loop = asyncio.get_event_loop()
    for agent in [researcher, calculator, clock, orchestrator]:
        loop.create_task(runtime.register(agent))

    all_tools = list(tools or [])
    return RunStreamAdapter(
        agent_id=orchestrator.id,
        runtime=runtime,
        tools=all_tools,
        correlation_id=session_id,
    )


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
    runtime: Any = None,
    enable_capability_search: bool = True,
) -> RunStreamAdapter:
    """Build and register a ReActAgent for this thread, return a stream adapter.

    The agent is registered with the shared Runtime and associated with
    the thread's session_id via the Message.correlation_id on submit.
    """
    if runtime is None:
        raise ValueError(
            "load_agent_for_thread() requires a runtime. "
            "Pass app.state.runtime from the server lifespan."
        )

    session_id = str(thread_id)

    # Seed history for this session from Postgres cold store
    memory = await load_session_memory(
        session_id=session_id,
        system_instructions=system_instructions,
        history=history,
        include_mcp_app_context=True,
        cold_store_name="Postgres",
        load_persisted_steps=lambda: load_messages_for_memory(db, thread_id),
    )

    if settings.AGENT_MODE.lower() == "orchestrator":
        return _build_orchestrator(
            model_client=model_client,
            runtime=runtime,
            model_context_window=model_context_window,
            tool_timeout=tool_timeout,
            session_id=session_id,
            tools=tools,
        )

    # Build the tool registry
    from ravi.agents.tools.toolbox import Toolbox
    toolbox = Toolbox()
    for t in tools:
        toolbox.register(t)

    agent = ReActAgent(
        f"assistant-{session_id[:8]}",
        model=model_client,
        tools=toolbox,
        context=ContextConfig(memory, SlidingWindowCompaction(max_messages=model_context_window)),
        system_instructions=system_instructions,
        max_iterations=max_iterations,
    )

    await runtime.register(agent)

    return RunStreamAdapter(
        agent_id=agent.id,
        runtime=runtime,
        tools=tools,
        correlation_id=session_id,
    )


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
    """Save an assistant message step and return its ID."""
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
    if (
        not tool_calls
        and hasattr(message, "content")
        and isinstance(message.content, list)
    ):
        tool_calls = [
            block for block in message.content if isinstance(block, ToolUseBlock)
        ]

    if tool_calls:
        serialized_tcs = []
        for tc in tool_calls:
            tc_name = getattr(tc, "name", getattr(tc, "tool_name", "unknown"))
            tc_data = tc.model_dump(mode="json") if hasattr(tc, "model_dump") else {}
            if tool_meta_map and tc_name in tool_meta_map:
                meta = tool_meta_map[tc_name]
                ui_info = meta.get("ui", {})
                resource_uri = ui_info.get("resourceUri", "")
                if resource_uri:
                    from ravi.serving.monolith.routes.mcp_apps import resolve_ui_uri
                    http_url = resolve_ui_uri(resource_uri) or resource_uri
                    tc_data["_meta"] = {
                        "ui": {"resourceUri": resource_uri, "httpUrl": http_url}
                    }
            serialized_tcs.append(tc_data)
        generation["tool_calls"] = serialized_tcs

    output_text = None
    if hasattr(message, "content") and message.content:
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
