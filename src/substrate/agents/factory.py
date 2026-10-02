"""Shared agent factory for monolith and distributed execution paths."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from substrate.tools.protocols import Tool
from substrate.models.protocols import ChatModel
from substrate.tools.approval import ApprovalHandler
from substrate.tools.protocols import ToolRisk
from substrate.context.compaction.pipeline import CompactionPipeline
from substrate.context.compaction.sliding_window import SlidingWindowCompaction
from substrate.middleware._contracts import Middleware
from substrate.middleware.pipeline import MiddlewarePipeline
from substrate.context.history import ThreadStore
from substrate.stores.local.threads import LocalFilesystemThreadStore

if TYPE_CHECKING:
    from substrate.agents.react import ReActAgent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Agent construction
# ---------------------------------------------------------------------------


def rebuild_agent(
    spec: dict,
    *,
    model_client: ChatModel,
    tools: list[Tool] | None = None,
) -> ReActAgent:
    """Reconstruct an agent from a persisted spec (used for cold resume).

    The spec is the dict saved at submit time:
    ``{mode, model, system_instructions, tool_names, max_iterations,
       session_id, model_context_window}``.

    ``tools`` should be the resolved tool objects (caller looks up by name
    from the live registry); any ``tool_names`` not found are silently dropped.

    Extra middleware isn't persisted in the spec (it's live Python objects,
    not JSON-serializable) — only the default tracing middleware is
    attached. A cold-resumed agent that needs more must be paired with a
    spec that records which ones to reattach; not needed by any caller today.
    """
    from substrate.agents.react import ReActAgent
    from substrate.context.context import ContextConfig
    from substrate.tools.toolbox import Toolbox

    session_id = spec.get("session_id", "resumed")
    max_iterations = spec.get("max_iterations", 30)
    model_context_window = spec.get("model_context_window", 40)
    system_instructions = spec.get("system_instructions", "")

    ctx = ContextConfig(
        LocalFilesystemThreadStore(),
        pipeline=CompactionPipeline(
            [SlidingWindowCompaction(max_messages=model_context_window)]
        ),
    )

    toolbox = Toolbox()
    for t in tools or []:
        toolbox.add(t)

    return ReActAgent(
        "assistant",
        session_id=session_id,
        model=model_client,
        tools=toolbox if tools else None,
        system_instructions=system_instructions,
        context=ctx,
        max_iterations=max_iterations,
    )


def create_assistant_agent(
    *,
    model_client: ChatModel,
    tools: list[Tool] | None = None,
    system_instructions: str = "",
    memory: ThreadStore | None = None,
    model_context: CompactionPipeline | None = None,
    model_context_window: int = 40,
    max_iterations: int = 30,
    tool_timeout: float | None = None,
    name: str = "ChatBot",
    session_id: str | None = None,
    middleware: list[Middleware] | None = None,
    initial_tool_choice: str | None = None,
    approval_handler: ApprovalHandler | None = None,
    approval_required_risk: ToolRisk | None = None,
) -> ReActAgent:
    """Create a configured ``ReActAgent``.

    The agent is returned unregistered — callers are responsible for calling
    ``await runtime.register(agent)`` before submitting work.  This keeps the
    factory free of runtime coupling and makes the construction path testable
    without a live runtime.

    Args:
        model_client: The LLM client to drive the ReAct loop.
        tools: Optional list of Tool instances to expose.
        system_instructions: System prompt prepended to every conversation.
        memory: Shared history provider; an ``InMemoryThreadStore`` is used
            when ``None``.
        model_context: Explicit compaction pipeline; if ``None`` a
            ``CompactionPipeline([SlidingWindowCompaction(max_messages=model_context_window)])`` is
            created automatically.
        model_context_window: Window size used when ``model_context`` is not
            provided.
        max_iterations: Maximum ReAct loop iterations per run.
        tool_timeout: Per-tool execution timeout in seconds (unused internally —
            passed through for caller convenience).
        name: Agent name / identifier.
        middleware: Extra middleware to attach, in wrap order (index 0 is
            outermost). Each middleware declares which stage(s) it applies
            to via a ``stages`` class attribute (see
            ``agents/middleware/pipeline.py``) — e.g.
            ``ContentFilterMiddleware``/``PromptInjectionMiddleware`` (TURN),
            ``MaxTokenMiddleware``/``HistoryTruncatorMiddleware``/
            ``RetryMiddleware``/``LLMJudgeMiddleware`` (CHAT),
            ``PIIDetectionMiddleware``/``ToolCallValidationMiddleware``/
            ``CacheMiddleware``/``ContentTruncatorMiddleware`` (TOOL).
        initial_tool_choice: Forces this exact tool name on the agent's
            first LLM call only; dropped after (see ``ReActAgent``).
        approval_handler: Satisfies ``kernel.tools.approval.ApprovalHandler``
            — pauses a tool call whose risk exceeds ``approval_required_risk``
            for a human decision. ``None`` means CRITICAL/HIGH-risk tools
            fail closed with "no ApprovalHandler configured" (see
            ``ToolInvoker``) rather than executing unguarded.
        approval_required_risk: The highest ``ToolRisk`` that executes
            without approval; anything above it requires one. ``None``
            leaves ``ToolInvoker``'s own default (see
            ``worker.py::_build_tool_invoker``).
    """
    from substrate.agents.react import ReActAgent
    from substrate.context.context import ContextConfig
    from substrate.tools.toolbox import Toolbox

    pipeline = model_context or CompactionPipeline(
        [SlidingWindowCompaction(max_messages=model_context_window)]
    )

    ctx = ContextConfig(
        memory if memory is not None else LocalFilesystemThreadStore(),
        pipeline=pipeline,
    )

    toolbox = Toolbox()
    for t in tools or []:
        toolbox.add(t)

    return ReActAgent(
        name,
        model=model_client,
        tools=toolbox if tools else None,
        system_instructions=system_instructions or "",
        context=ctx,
        max_iterations=max_iterations,
        session_id=session_id,
        initial_tool_choice=initial_tool_choice,
        approval_handler=approval_handler,
        approval_required_risk=approval_required_risk,
        middleware=MiddlewarePipeline(list(middleware or [])),
    )

