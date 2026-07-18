"""ReActAgent — ReAct reasoning loop on the durable kernel."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from substrate.kernel.core.content import (
    ChatMessage,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from substrate.kernel.core.identity import AgentId, TopicId
from substrate.kernel.llm.llm import GenerationOptions, LLMResponse
from substrate.kernel.messaging.message import Message
from substrate.kernel.storage.history import HistoryProvider
from substrate.kernel.tools import ToolRegistry
from substrate.kernel.tools.approval import ApprovalHandler
from substrate.kernel.tools.tools import ToolRisk

from substrate.agents.context.context import ContextConfig
from substrate.agents.resources.budget import ExecutionTracker
from substrate.agents.hooks.manager import HookEvent, HookManager
from substrate.agents.middleware._contracts import (
    AgentRunResult,
    MiddlewareContext,
    ToolCallRecord,
)
from substrate.agents.middleware.pipeline import MiddlewarePipeline
from substrate.kernel.agent.middleware import MiddlewareStage
from substrate.agents.storage.tasks import (
    current_agent_id as _task_agent_id,
    current_agent_label as _task_agent_label,
    current_parent_agent_id as _task_parent_agent_id,
    current_thread_id as _task_thread_id,
    current_user_id as _task_user_id,
)
from substrate.agents.core._loop import (
    deliver,
    final_text,
    load_history,
    log_user_message,
    message_to_chat,
    persist_turns,
)

if TYPE_CHECKING:
    from substrate.agents.runtime.context import RunContext
    from substrate.kernel.llm.llm import LLMClient


class ReActAgent:
    """ReAct loop agent implementing the Agent protocol.

    ``model``, ``tools``, ``approval_handler``, and ``approval_required_risk``
    are read by the Worker when building the per-run RunContext.

    ``middleware`` (``MiddlewarePipeline``) is the one middleware pipeline
    for this agent. It's dispatched at three different moments — once per
    inbox message/turn (``MiddlewareStage.TURN``, wrapping the whole ReAct
    loop for that turn, in this file's ``_handle_message()``), around every
    ``ctx.llm()`` call (``MiddlewareStage.CHAT``, in
    ``agents/runtime/context.py``), and around every ``ctx.tool()`` call
    (``MiddlewareStage.TOOL``, same file) — but it's the identical pipeline
    object and the identical ``Middleware.process(context, call_next)``
    shape every time. A middleware that only cares about one stage (e.g.
    ``PIIDetectionMiddleware`` only cares about TOOL) declares that via a
    ``stages`` class attribute; see ``agents/middleware/pipeline.py``.
    """

    def __init__(
        self,
        name: str,
        *,
        model: LLMClient | None = None,
        tools: ToolRegistry | list | None = None,
        context: ContextConfig | None = None,
        system_instructions: str = "",
        max_iterations: int = 10,
        output_topic: TopicId | None = None,
        approval_handler: ApprovalHandler | None = None,
        approval_required_risk: ToolRisk | None = None,
        execution_budget: ExecutionTracker | None = None,
        hooks: HookManager | None = None,
        middleware: MiddlewarePipeline | None = None,
        initial_tool_choice: str | None = None,
        session_id: str | None = None,
    ) -> None:
        self.id = AgentId(
            type="agent", key=f"{name}-{session_id}" if session_id else name
        )
        self.name = name
        self.model = model

        if isinstance(tools, list):
            from substrate.agents.tools.toolbox import Toolbox

            tb = Toolbox()
            for t in tools:
                tb.add(t)
            self.tools = tb
        else:
            self.tools = tools

        self._context = context or ContextConfig.default()
        self._system_instructions = system_instructions
        self._max_iterations = max_iterations
        self._output_topic = output_topic
        self.approval_handler = approval_handler
        self.approval_required_risk = approval_required_risk
        self._execution_budget = execution_budget
        self.hooks = hooks
        self.middleware = middleware or MiddlewarePipeline()
        self._initial_tool_choice = initial_tool_choice

    @property
    def history(self) -> HistoryProvider:
        return self._context.history

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        _task_agent_id.set(str(self.id))
        _task_agent_label.set(self.name)
        for msg in inbox:
            ctx.check()
            await self._handle_message(ctx, msg)

    async def _handle_message(self, ctx: RunContext, msg: Message) -> None:
        session_id = msg.correlation_id or ctx.run_id
        # Stamp thread_id so TaskManagerTool scopes boards to this conversation.
        # Must be set here (inside the Worker task) because the Worker runs in a
        # different asyncio context from the SSE generator where the ContextVar
        # was previously attempted.
        _task_thread_id.set(session_id)
        # When spawned as a subagent, the orchestrator passes its own id in the
        # boot message metadata. Stamp it here (the spawning ContextVar does not
        # cross into this Worker task) so this agent's board nests under its
        # parent in the UI. Absent metadata ⇒ root agent.
        _task_parent_agent_id.set(msg.metadata.get("parent_agent_id") or None)
        # Same cross-task-boundary reasoning as thread_id/parent_agent_id above:
        # the chat route stamps user_id into boot metadata, and the
        # code-interpreter tool reads this ContextVar to pick the caller's
        # workspace subPath (see agents/storage/tasks.py::current_user_id).
        _task_user_id.set(msg.metadata.get("user_id") or None)

        history_messages = await load_history(self._context, self.id, session_id)
        user_turn = message_to_chat(msg)
        await log_user_message(ctx, msg, user_turn)
        messages: list[ChatMessage] = history_messages + [user_turn]

        call_ctx = MiddlewareContext(
            stage=MiddlewareStage.TURN,
            agent_name=self.name,
            run_id=ctx.run_id,
            session_id=session_id,
            messages=messages,
        )

        async def _final(c: MiddlewareContext) -> None:
            c.turn_result = await self._react_loop(
                ctx, msg, session_id, messages, len(history_messages)
            )

        await self.middleware.execute(call_ctx, _final)

    def _resolve_execution_budget(self, ctx: RunContext) -> ExecutionTracker | None:
        """Effective budget for this run: the run's inherited ``Supervision.
        execution_budget`` (set when this agent was ``ctx.spawn()``'d under a
        budget, propagated via ``Supervision.spawn_child()``) takes priority
        over the constructor-supplied default.

        Built fresh per call rather than cached on ``self`` — an ``Agent``
        instance is registered once and reused across every run of its
        ``agent_id`` (see ``Runtime.register()``); a shared, mutating
        ``ExecutionTracker`` on ``self`` would let concurrent runs of the same
        registered agent corrupt each other's usage counters. Rebuilding per
        call is also replay-safe: a resumed run re-enters this loop from the
        top and reprocesses every prior turn (cache hits included), so a
        fresh tracker still ends up with the correct cumulative total.
        """
        supervision = ctx.meta.supervision
        budget = supervision.execution_budget if supervision else None
        if budget is not None:
            return ExecutionTracker(
                max_tokens=budget.max_tokens,
                max_cost_usd=budget.max_cost_usd,
                max_turns=budget.max_turns,
            )
        return self._execution_budget

    async def _generate_turn(
        self,
        ctx: RunContext,
        messages: list[ChatMessage],
        options: GenerationOptions,
        tracker: ExecutionTracker | None,
    ) -> LLMResponse:
        """One LLM call for the loop: dispatch hooks, compact, call, track budget."""
        if self.hooks:
            await self.hooks.dispatch(
                HookEvent.LLM_START, {"agent_name": self.name, "run_id": ctx.run_id}
            )
        # Compact before each LLM call so tool results don't inflate the
        # context unboundedly across iterations.  We compact a *view* of
        # messages here and keep the full list intact for persistence.
        llm_messages = await self._context.pipeline.compact(messages)
        resp = await ctx.llm(llm_messages, options=options)
        if self.hooks:
            await self.hooks.dispatch(
                HookEvent.LLM_END,
                {"agent_name": self.name, "run_id": ctx.run_id, "usage": resp.usage},
            )
        if tracker is not None:
            tracker.consume(
                tokens=resp.usage.total_tokens if resp.usage else 0,
                turns=1,
            )
        return resp

    async def _execute_tool_calls(
        self, ctx: RunContext, tool_calls: list[ToolUseBlock]
    ) -> tuple[list[ToolResultBlock], list[ToolCallRecord]]:
        """Invoke each requested tool call in turn, building wire results + records."""
        results: list[ToolResultBlock] = []
        records: list[ToolCallRecord] = []
        for tc in tool_calls:
            ctx.check()
            t0 = time.monotonic()
            inv_result = await ctx.tool(tc.tool_name, **tc.arguments)
            duration_ms = (time.monotonic() - t0) * 1000
            is_error = inv_result.status != "ok"
            results.append(
                ToolResultBlock(
                    call_id=tc.call_id,
                    content=[TextBlock(text=inv_result.text or "")],
                    is_error=is_error,
                )
            )
            records.append(
                ToolCallRecord(
                    name=tc.tool_name,
                    call_id=tc.call_id,
                    arguments=tc.arguments,
                    result=inv_result.text or "",
                    is_error=is_error,
                    duration_ms=duration_ms,
                )
            )
        return results, records

    async def _react_loop(
        self,
        ctx: RunContext,
        msg: Message,
        session_id: str,
        messages: list[ChatMessage],
        n_loaded: int,
    ) -> AgentRunResult:
        tool_list = self.tools.all() if self.tools else []
        base_options = GenerationOptions(
            system_instructions=self._system_instructions,
            tools=tool_list or None,
        )
        # Apply initial_tool_choice only to the very first LLM call.
        options = (
            GenerationOptions(
                system_instructions=self._system_instructions,
                tools=tool_list or None,
                tool_choice=self._initial_tool_choice,
            )
            if self._initial_tool_choice
            else base_options
        )
        tracker = self._resolve_execution_budget(ctx)

        tool_call_records: list[ToolCallRecord] = []

        for _ in range(self._max_iterations):
            ctx.check()
            resp = await self._generate_turn(ctx, messages, options, tracker)
            # Drop the forced tool_choice after the first call so subsequent
            # iterations can freely choose to respond with text or more tools.
            options = base_options

            assistant_turn = ChatMessage(role=Role.ASSISTANT, content=resp.content)
            messages.append(assistant_turn)

            tool_calls = [b for b in resp.content if isinstance(b, ToolUseBlock)]
            if not tool_calls:
                break

            results, records = await self._execute_tool_calls(ctx, tool_calls)
            tool_call_records.extend(records)
            messages.append(ChatMessage(role=Role.TOOL, content=results))  # type: ignore[arg-type]
        else:
            from substrate.kernel.core.errors import BudgetExhaustedError

            raise BudgetExhaustedError(
                f"Agent reached max iterations limit ({self._max_iterations})"
            )

        new_turns = messages[n_loaded:]
        await persist_turns(self._context, self.id, session_id, ctx.run_id, new_turns)

        ans = final_text(messages)
        await deliver(
            ctx, msg, {"text": ans}, sender=self.id, output_topic=self._output_topic
        )

        return AgentRunResult(
            output=ans,
            status="success",
            tool_calls=tool_call_records,
            run_id=ctx.run_id,
        )


__all__ = ["ReActAgent"]
