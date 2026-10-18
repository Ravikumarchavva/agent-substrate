"""How the server builds its assistant agents, the same way in every role."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from loamz.agents.kernel.context.compaction.pipeline import CompactionPipeline
from loamz.agents.kernel.context.compaction.sliding_window import SlidingWindowCompaction
from loamz.agents.kernel.context.history import ThreadStore
from loamz.agents.kernel.middleware._contracts import Middleware
from loamz.agents.kernel.middleware.pipeline import MiddlewarePipeline
from loamz.agents.kernel.models.protocols import ChatModel, ReasoningEffort
from loamz.agents.kernel.tools.approval import ApprovalHandler
from loamz.agents.kernel.tools.protocols import Tool, ToolRisk

if TYPE_CHECKING:
    from loamz.agents.kernel.agents.channel import ChannelMemberConfig
    from loamz.agents.kernel.agents.react import ReActAgent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Agent construction
# ---------------------------------------------------------------------------


def rebuild_agent(
    spec: dict,
    *,
    model_client: ChatModel,
    history: ThreadStore,
    tools: list[Tool] | None = None,
) -> ReActAgent:
    """Reconstruct an agent from a persisted spec (used for cold resume), keeping its history in ``history``.

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
    from loamz.agents.kernel.agents.react import ReActAgent
    from loamz.agents.kernel.context.context import ContextConfig
    from loamz.agents.kernel.tools.toolbox import Toolbox

    session_id = spec.get("session_id", "resumed")
    max_iterations = spec.get("max_iterations", 30)
    model_context_window = spec.get("model_context_window", 40)
    system_instructions = spec.get("system_instructions", "")

    ctx = ContextConfig(
        history,
        pipeline=CompactionPipeline([SlidingWindowCompaction(max_messages=model_context_window)]),
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
    history: ThreadStore,
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
    reasoning: ReasoningEffort | None = None,
    channel: ChannelMemberConfig | None = None,
) -> ReActAgent:
    """Create a configured ``ReActAgent`` (a ``ChannelMemberAgent`` when ``channel`` is given: one that
    lives in channels, woken by what is said there and choosing whether to answer).

    The agent is returned unregistered — callers are responsible for calling
    ``await runtime.register(agent)`` before submitting work.  This keeps the
    factory free of runtime coupling and makes the construction path testable
    without a live runtime.

    Args:
        model_client: The LLM client to drive the ReAct loop.
        tools: Optional list of Tool instances to expose.
        system_instructions: System prompt prepended to every conversation.
        history: Where the agent keeps its conversations (``AgentStore.threads``, or a tenant's view of them).
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
        reasoning: How hard a reasoning-capable model thinks before answering (``None`` = the provider default).
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
    from loamz.agents.kernel.agents.react import ReActAgent
    from loamz.agents.kernel.context.context import ContextConfig
    from loamz.agents.kernel.tools.toolbox import Toolbox

    pipeline = model_context or CompactionPipeline(
        [SlidingWindowCompaction(max_messages=model_context_window)]
    )

    ctx = ContextConfig(
        history,
        pipeline=pipeline,
    )

    toolbox = Toolbox()
    for t in tools or []:
        toolbox.add(t)

    build = {
        "model": model_client,
        "tools": toolbox if tools else None,
        "system_instructions": system_instructions or "",
        "context": ctx,
        "max_iterations": max_iterations,
        "session_id": session_id,
        "initial_tool_choice": initial_tool_choice,
        "approval_handler": approval_handler,
        "approval_required_risk": approval_required_risk,
        "middleware": MiddlewarePipeline(list(middleware or [])),
        "reasoning": reasoning,
    }
    if channel is not None:
        from loamz.agents.kernel.agents.channel import ChannelMemberAgent

        return ChannelMemberAgent(name, channel=channel, **build)  # type: ignore[arg-type]
    return ReActAgent(name, **build)  # type: ignore[arg-type]
