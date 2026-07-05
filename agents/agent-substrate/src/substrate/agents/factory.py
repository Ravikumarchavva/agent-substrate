"""Shared agent factory for monolith and distributed execution paths."""

from __future__ import annotations

from substrate.logger import setup_logging

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from substrate.agents.context import (
    HistoryProvider,
    InMemoryHistoryProvider,
    SlidingWindowCompaction,
    CompactionPipeline,
)
from substrate.kernel.llm import LLMClient
from substrate.kernel import (
    ChatMessage,
    ContentBlock,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    Tool,
)
from substrate.kernel.core.identity import AgentId
from substrate.agents.middleware._contracts import Middleware
from substrate.agents.middleware.observability import (
    AgentTracingMiddleware,
    ChatTracingMiddleware,
    FunctionTracingMiddleware,
)
from substrate.agents.middleware.pipeline import MiddlewarePipeline

if TYPE_CHECKING:
    from substrate.agents.core import ReActAgent, OrchestratorAgent

logger = setup_logging()


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

PersistedStepLoader = Callable[[], Awaitable[list[dict]]]


# ---------------------------------------------------------------------------
# Session history loading
# ---------------------------------------------------------------------------


async def rebuild_messages_from_steps(
    step_rows: list[dict],
    system_instructions: str,
    *,
    include_mcp_app_context: bool = False,
) -> list[ChatMessage]:
    """Rebuild framework messages from persisted step rows using unified ChatMessage."""
    messages: list[ChatMessage] = []
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
            tool_content: list[ContentBlock] = [
                ToolResultBlock(
                    call_id=call_id,
                    name=tool_name,
                    content=[TextBlock(text=output_text)],
                    is_error=is_error,
                )
            ]
            messages.append(ChatMessage(role="tool", content=tool_content))
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
    history: HistoryProvider | None = None,
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
        hit = await history.count_messages(_aid, session_id=session_id) > 0

        if hit:
            logger.debug("History hit for %s", session_id)
            return history

        logger.debug(
            "History miss for %s — loading from %s", session_id, cold_store_name
        )

        # Idempotent seed: guard against two concurrent callers (two
        # replicas, or two racing requests before any single-flight check
        # engages) both observing the miss above and both seeding — a
        # double-seed silently truncates older messages once the provider's
        # per-session cap kicks in (see RedisHistoryProvider.
        # try_acquire_seed_lock's docstring). Only providers that expose the
        # lock primitive are guarded; others (in-memory, single-process
        # test doubles) have no cross-process race to guard against.
        acquire_lock = getattr(history, "try_acquire_seed_lock", None)
        if acquire_lock is None:
            await _seed(history)
            return history

        if await acquire_lock(_aid, session_id):
            await _seed(history)
            return history

        # Lost the race — someone else is seeding (or just finished).
        # Wait for their write to land instead of proceeding with a
        # possibly-still-empty history.
        for _ in range(50):
            if await history.count_messages(_aid, session_id=session_id) > 0:
                break
            await asyncio.sleep(0.1)
        else:
            logger.warning(
                "Timed out waiting for concurrent seed of session %s", session_id
            )
        return history

    fallback = InMemoryHistoryProvider()
    await _seed(fallback)
    return fallback


# ---------------------------------------------------------------------------
# Agent construction
# ---------------------------------------------------------------------------


def rebuild_agent(
    spec: dict,
    *,
    model_client: LLMClient,
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
    from substrate.agents.core import ReActAgent
    from substrate.agents.tools.toolbox import Toolbox
    from substrate.agents.context import ContextConfig

    session_id = spec.get("session_id", "resumed")
    max_iterations = spec.get("max_iterations", 30)
    model_context_window = spec.get("model_context_window", 40)
    system_instructions = spec.get("system_instructions", "")

    ctx = ContextConfig(
        InMemoryHistoryProvider(),
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
        middleware=MiddlewarePipeline(
            [
                AgentTracingMiddleware(),
                ChatTracingMiddleware(),
                FunctionTracingMiddleware(),
            ]
        ),
    )


def create_assistant_agent(
    *,
    model_client: LLMClient,
    tools: list[Tool] | None = None,
    system_instructions: str = "",
    memory: HistoryProvider | None = None,
    model_context: CompactionPipeline | None = None,
    model_context_window: int = 40,
    max_iterations: int = 30,
    tool_timeout: float | None = None,
    name: str = "ChatBot",
    session_id: str | None = None,
    middleware: list[Middleware] | None = None,
    initial_tool_choice: str | None = None,
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
        memory: Shared history provider; an ``InMemoryHistoryProvider`` is used
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
            Appended after the built-in tracing middlewares
            (``AgentTracingMiddleware``/``ChatTracingMiddleware``/
            ``FunctionTracingMiddleware``), which stay outermost so a
            ``MiddlewareTermination`` from a caller-supplied middleware
            still produces an ERROR-tagged span.
        initial_tool_choice: Forces this exact tool name on the agent's
            first LLM call only; dropped after (see ``ReActAgent``).
    """
    from substrate.agents.core import ReActAgent
    from substrate.agents.tools.toolbox import Toolbox
    from substrate.agents.context import ContextConfig

    pipeline = model_context or CompactionPipeline(
        [SlidingWindowCompaction(max_messages=model_context_window)]
    )

    ctx = ContextConfig(
        memory if memory is not None else InMemoryHistoryProvider(),
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
        middleware=MiddlewarePipeline(
            [
                AgentTracingMiddleware(),
                ChatTracingMiddleware(),
                FunctionTracingMiddleware(),
                *(middleware or []),
            ]
        ),
    )


# ---------------------------------------------------------------------------
# Token-budget compaction (chat-facing agents)
# ---------------------------------------------------------------------------


def build_token_budget_pipeline(*, token_budget: int = 50_000) -> CompactionPipeline:
    """Compaction pipeline used by chat-facing agents: trims tool results and
    older tool-call groups before truncating outright, keeping recent
    context intact until the token budget is actually under pressure."""
    from substrate.agents.context import (
        SelectiveToolCallCompactionStrategy,
        TokenBudgetComposedStrategy,
        ToolResultCompactionStrategy,
        TruncationStrategy,
    )

    return CompactionPipeline(
        [
            TokenBudgetComposedStrategy(
                strategies=[
                    ToolResultCompactionStrategy(max_chars=1500),
                    SelectiveToolCallCompactionStrategy(keep_recent_groups=5),
                    TruncationStrategy(max_chars=200_000),
                ],
                token_budget=token_budget,
            )
        ]
    )


# ---------------------------------------------------------------------------
# Research orchestrator (fixed researcher/calculator/clock topology)
# ---------------------------------------------------------------------------


@dataclass
class ResearchOrchestrator:
    """The coordinator plus its three sub-agents — all four must be
    registered with the Runtime before submitting work to the coordinator."""

    coordinator: "OrchestratorAgent"
    sub_agents: list["ReActAgent"]

    @property
    def all_agents(self) -> list["ReActAgent | OrchestratorAgent"]:
        return [self.coordinator, *self.sub_agents]


def build_research_orchestrator(
    *,
    model_client: LLMClient,
    researcher_tools: list[Tool],
    calculator_tools: list[Tool],
    clock_tools: list[Tool],
) -> ResearchOrchestrator:
    """Build the fixed researcher/calculator/clock/coordinator topology used
    when ``AGENT_MODE=orchestrator``.

    Tools are passed in rather than constructed here — agents/ must not
    import capabilities/ (``WebSearchTool``, ``CalculatorTool``, etc. all
    live in ``capabilities/tools/``), so the caller (``infrastructure/
    serving_factory.py``, which is allowed to cross both layers) builds the
    concrete tool instances and hands them to each specialist.

    Returned unregistered, like ``create_assistant_agent`` — the caller
    (which owns the ``Runtime``) is responsible for registering every agent
    in ``.all_agents`` before submitting work to the coordinator.
    """
    from substrate.agents.core import OrchestratorAgent, SubAgentConfig
    from substrate.agents.context import ContextConfig

    researcher = create_assistant_agent(
        name="researcher",
        model_client=model_client,
        tools=researcher_tools,
        system_instructions="You are a research specialist.",
        model_context=build_token_budget_pipeline(),
        max_iterations=5,
    )
    calculator = create_assistant_agent(
        name="calculator",
        model_client=model_client,
        tools=calculator_tools,
        system_instructions="You are a calculation specialist.",
        model_context=build_token_budget_pipeline(),
        max_iterations=3,
    )
    clock = create_assistant_agent(
        name="clock",
        model_client=model_client,
        tools=clock_tools,
        system_instructions="You are a time specialist.",
        model_context=build_token_budget_pipeline(),
        max_iterations=2,
    )
    orchestrator = OrchestratorAgent(
        "coordinator",
        model=model_client,
        sub_agents=[
            SubAgentConfig(
                researcher, description="Searches the web.", ask_timeout=60.0
            ),
            SubAgentConfig(
                calculator, description="Performs calculations.", ask_timeout=30.0
            ),
            SubAgentConfig(
                clock, description="Reports the current time.", ask_timeout=10.0
            ),
        ],
        max_iterations=15,
        context=ContextConfig(
            InMemoryHistoryProvider(), pipeline=build_token_budget_pipeline()
        ),
    )
    # Display names only — AgentId routing keys stay lowercase (unchanged
    # from before this was extracted from infrastructure/serving_factory.py).
    researcher.name = "Researcher"
    calculator.name = "Calculator"
    clock.name = "Clock"
    orchestrator.name = "Coordinator"
    return ResearchOrchestrator(
        coordinator=orchestrator, sub_agents=[researcher, calculator, clock]
    )
