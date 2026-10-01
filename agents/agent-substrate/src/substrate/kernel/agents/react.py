"""ReActAgent — ReAct reasoning loop on the durable kernel."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from substrate.kernel.abstractions.agent.context import (
    CompactionContext,
    CompactionPhase,
)
from substrate.kernel.abstractions.agent.middleware import MiddlewareStage
from substrate.kernel.abstractions.agent.runtime_context import RunScope
from substrate.kernel.abstractions.agent.supervision import ExecutionBudget
from substrate.kernel.abstractions.core.content import (
    ChatMessage,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from substrate.kernel.abstractions.core.finish_reason import FinishReason
from substrate.kernel.abstractions.core.identity import Actor, Topic
from substrate.kernel.abstractions.exceptions import (
    BudgetExhaustedError,
    ContentFilterError,
    ContextLengthError,
)
from substrate.kernel.abstractions.llm.llm import (
    GenerationOptions,
    LLMResponse,
    ReasoningEffort,
)
from substrate.kernel.abstractions.messaging.message import (
    ChatPayload,
    DataPayload,
    Message,
)
from substrate.kernel.abstractions.runtime.log_entry import RunLogKind
from substrate.kernel.abstractions.storage.history import HistoryProvider
from substrate.kernel.abstractions.tools import ToolRegistry, is_concurrency_safe
from substrate.kernel.abstractions.tools.approval import ApprovalHandler
from substrate.kernel.abstractions.tools.chain import ChainPolicy
from substrate.kernel.abstractions.tools.tools import ToolRisk
from substrate.kernel.agents.base import BaseAgent
from substrate.kernel.agents.routed import handle
from substrate.kernel.context.compaction.sliding_window import SlidingWindowCompaction
from substrate.kernel.context.context import ContextConfig
from substrate.kernel.hooks.manager import HookEvent, HookManager
from substrate.kernel.middleware._contracts import (
    AgentRunResult,
    MiddlewareContext,
    ToolCallRecord,
)
from substrate.kernel.middleware.pipeline import MiddlewarePipeline

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from substrate.kernel.abstractions.llm.llm import LLMClient
    from substrate.kernel.abstractions.tools.chain import InvocationResult
    from substrate.kernel.runtime.context import RunContext


class ReActAgent(BaseAgent):
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
        output_topic: Topic | None = None,
        approval_handler: ApprovalHandler | None = None,
        approval_required_risk: ToolRisk | None = None,
        execution_budget: ExecutionBudget | None = None,
        hooks: HookManager | None = None,
        middleware: MiddlewarePipeline | None = None,
        initial_tool_choice: str | None = None,
        session_id: str | None = None,
        reasoning: ReasoningEffort | None = None,
        tool_policy: ChainPolicy | None = None,
    ) -> None:
        self.id = Actor(type=name, key=session_id or "")
        self.name = name
        self.model = model

        if isinstance(tools, list):
            from substrate.kernel.tools.toolbox import Toolbox

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
        self.execution_budget = execution_budget  # what the engine enforces when no budget was inherited
        self.hooks = hooks
        self.middleware = middleware or MiddlewarePipeline()
        self._initial_tool_choice = initial_tool_choice
        self._reasoning = reasoning
        # Read by the Worker when it builds this agent's ToolInvoker.
        self.tool_policy = tool_policy

    @property
    def history(self) -> HistoryProvider:
        return self._context.history

    @handle(ChatPayload, DataPayload)
    async def _handle_message(self, ctx: RunContext, msg: Message) -> None:
        session_id = msg.correlation_id or ctx.run_id
        # Who this message belongs to — tenant/user/branch come from its
        # metadata (stamped by the caller, or by a parent agent via
        # ``RunScope.child_metadata``). Set on ctx, never on ambient globals:
        # tools read ``ctx.scope``.
        ctx.set_scope(
            RunScope.from_metadata(
                msg.metadata,
                thread_id=session_id,
                agent_id=str(self.id),
                agent_label=self.name,
            )
        )
        branch_id = ctx.scope.branch_id
        history_messages = await self._load_history(
            self._context, session_id, branch_id=branch_id
        )
        user_turn = self._message_to_chat(msg)
        user_message_seq = await self._log_user_message(ctx, msg, user_turn)
        messages: list[ChatMessage] = history_messages + [user_turn]

        call_ctx = MiddlewareContext(
            stage=MiddlewareStage.TURN,
            agent_name=self.name,
            run_id=ctx.run_id,
            session_id=session_id,
            messages=messages,
            user_message_seq=user_message_seq,
            run_context=ctx,
        )

        async def _final(c: MiddlewareContext) -> None:
            c.turn_result = await self._react_loop(
                ctx,
                msg,
                session_id,
                messages,
                len(history_messages),
                branch_id=branch_id,
            )

        await self.middleware.execute(call_ctx, _final)

    async def _generate_turn(
        self,
        ctx: RunContext,
        messages: list[ChatMessage],
        options: GenerationOptions,
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
        try:
            resp = await ctx.llm(llm_messages, options=options)
        except ContextLengthError:
            # The prompt did not fit. Say so to the pipeline's own strategies is not enough —
            # they already ran — so halve what is sent (oldest first, never splitting a tool
            # call from its result) and try once more. A second overflow is real and propagates.
            smaller = await SlidingWindowCompaction(max_messages=max(2, len(llm_messages) // 2)).compact(llm_messages)
            if len(smaller) >= len(llm_messages):
                raise
            logger.warning("context window exceeded; retrying with %d of %d messages", len(smaller), len(llm_messages))
            resp = await ctx.llm(smaller, options=options)
        if self.hooks:
            await self.hooks.dispatch(
                HookEvent.LLM_END,
                {"agent_name": self.name, "run_id": ctx.run_id, "usage": resp.usage},
            )
        return resp

    def _batchable(self, tool_calls: list[ToolUseBlock]) -> bool:
        """Run a turn's calls concurrently only when every one of them is a
        tool that declared itself concurrency-safe."""
        if len(tool_calls) < 2 or self.tools is None:
            return False
        return all(is_concurrency_safe(self.tools.get(tc.tool_name)) for tc in tool_calls)

    async def _execute_tool_calls(
        self, ctx: RunContext, tool_calls: list[ToolUseBlock]
    ) -> tuple[list[ToolResultBlock], list[ToolCallRecord]]:
        """Run the turn's tool calls, returning results in the model's call order.

        A call whose arguments weren't valid JSON is answered with an error
        (so the model can retry) instead of running the tool.
        """
        runnable = [tc for tc in tool_calls if tc.arguments_error is None]
        outcomes: dict[str, tuple[InvocationResult, float]] = {}
        if self._batchable(runnable):
            ctx.check()
            t0 = time.monotonic()
            batch = await ctx.tool_batch([(tc.tool_name, tc.arguments) for tc in runnable])
            elapsed_ms = (time.monotonic() - t0) * 1000
            outcomes = {tc.call_id: (r, elapsed_ms) for tc, r in zip(runnable, batch)}
        else:
            for tc in runnable:
                ctx.check()
                t0 = time.monotonic()
                r = await ctx.tool(tc.tool_name, tc.arguments)
                outcomes[tc.call_id] = (r, (time.monotonic() - t0) * 1000)

        results: list[ToolResultBlock] = []
        records: list[ToolCallRecord] = []
        for tc in tool_calls:
            if tc.call_id in outcomes:
                inv, duration_ms = outcomes[tc.call_id]
                is_error = inv.status != "ok"
                text = inv.text or ""
                # inv.media (e.g. matplotlib charts from code_interpreter)
                # rides along so the model sees what the tool produced; each
                # LLM client encodes it for its API and drops it (with a
                # note) for a model that can't see that modality.
                content = [TextBlock(text=text), *inv.media]
            else:
                is_error, duration_ms = True, 0.0
                text = f"Tool call not run: {tc.arguments_error}. Retry with valid JSON arguments."
                content = [TextBlock(text=text)]
            results.append(
                ToolResultBlock(
                    call_id=tc.call_id,
                    name=tc.tool_name,
                    content=content,  # type: ignore[arg-type]
                    is_error=is_error,
                )
            )
            records.append(
                ToolCallRecord(
                    name=tc.tool_name,
                    call_id=tc.call_id,
                    arguments=tc.arguments,
                    result=text,
                    is_error=is_error,
                    duration_ms=duration_ms,
                )
            )
        return results, records

    async def _wrap_up(
        self,
        ctx: RunContext,
        messages: list[ChatMessage],
        tool_list: list,
    ) -> ChatMessage:
        """Out of steps: one last call with tools disabled so the user gets an
        answer from what was gathered instead of nothing. The nudge is sent
        for this call only and never persisted."""
        nudge = ChatMessage(
            role=Role.USER,
            content=[
                TextBlock(
                    text=(
                        f"You have used all {self._max_iterations} steps for this turn. "
                        "Do not call any more tools. Reply now with your best answer from "
                        "what you have so far, and say plainly what is left unfinished."
                    )
                )
            ],
        )
        options = GenerationOptions(
            system_instructions=self._system_instructions,
            reasoning=self._reasoning,
            tools=tool_list or None,
            tool_choice="none" if tool_list else None,
        )
        resp = await self._generate_turn(ctx, [*messages, nudge], options)
        content = [b for b in resp.content if not isinstance(b, ToolUseBlock)]
        if not any(isinstance(b, TextBlock) and b.text.strip() for b in content):
            content.append(
                TextBlock(text=f"Stopped after {self._max_iterations} steps without a final answer.")
            )
        return ChatMessage(role=Role.ASSISTANT, content=content)

    async def _persist(
        self,
        ctx: RunContext,
        session_id: str,
        messages: list[ChatMessage],
        n_loaded: int,
        branch_id: str,
    ) -> None:
        await self._persist_turns(
            self._context,
            session_id,
            ctx.run_id,
            messages[n_loaded:],
            branch_id=branch_id,
            workspace_snapshot_id=ctx.latest_workspace_snapshot_id,
        )

    async def _compact_after_turn(
        self, session_id: str, branch_id: str, messages: list[ChatMessage]
    ) -> None:
        """Run the POST_TURN compaction phase and persist any checkpoint it
        proposes. Best effort: the turn's answer already exists, so a failure
        here is logged with its traceback rather than failing the turn."""
        coordinator = self._context.coordinator
        if coordinator is None:
            return
        history = self._context.history
        try:
            branch = await history.get_branch(session_id, branch_id)
            result = await coordinator.compact(
                CompactionPhase.POST_TURN,
                CompactionContext(
                    session_id=session_id,
                    branch_id=branch_id,
                    messages=messages,
                    leaf_node_id=branch.head_message_id if branch else None,
                ),
            )
            if result.checkpoint_proposal is not None:
                await history.save_checkpoint(result.checkpoint_proposal)
        except Exception:
            logger.exception("Post-turn compaction failed; the turn is unaffected")

    async def _react_loop(
        self,
        ctx: RunContext,
        msg: Message,
        session_id: str,
        messages: list[ChatMessage],
        n_loaded: int,
        *,
        branch_id: str = "main",
    ) -> AgentRunResult:
        tool_list = self.tools.all() if self.tools else []
        base_options = GenerationOptions(
            system_instructions=self._system_instructions,
            reasoning=self._reasoning,
            tools=tool_list or None,
        )
        # Apply initial_tool_choice only to the very first LLM call.
        options = (
            GenerationOptions(
                system_instructions=self._system_instructions,
                reasoning=self._reasoning,
                tools=tool_list or None,
                tool_choice=self._initial_tool_choice,
            )
            if self._initial_tool_choice
            else base_options
        )
        tool_call_records: list[ToolCallRecord] = []
        status = "success"

        try:
            for _ in range(self._max_iterations):
                ctx.check()
                resp = await self._generate_turn(ctx, messages, options)
                # Drop the forced tool_choice after the first call so subsequent
                # iterations can freely choose to respond with text or more tools.
                options = base_options

                if resp.finish_reason == FinishReason.CONTENT_FILTER:
                    raise ContentFilterError("the provider withheld the response on content grounds")

                assistant_turn = ChatMessage(role=Role.ASSISTANT, content=resp.content)
                messages.append(assistant_turn)

                tool_calls = [b for b in resp.content if isinstance(b, ToolUseBlock)]
                if not tool_calls:
                    if resp.finish_reason == FinishReason.LENGTH:
                        # A reply cut off at the token limit reads like a finished one. Say so.
                        status = "truncated"
                        await ctx.log_once(RunLogKind.RUN_TRUNCATED, {"reason": "max_tokens"})
                    break

                results, records = await self._execute_tool_calls(ctx, tool_calls)
                tool_call_records.extend(records)
                messages.append(ChatMessage(role=Role.TOOL, content=results))  # type: ignore[arg-type]
            else:
                messages.append(await self._wrap_up(ctx, messages, tool_list))
                status = "max_iterations"
                await ctx.log_once(
                    RunLogKind.RUN_TRUNCATED,
                    {"reason": "max_iterations", "max_iterations": self._max_iterations},
                )
        except BudgetExhaustedError as exc:
            # The run still fails, but the conversation isn't erased: keep what
            # was said and done this turn so the next message has its context.
            messages.append(
                ChatMessage(
                    role=Role.ASSISTANT,
                    content=[TextBlock(text=f"[Stopped before finishing: {exc}]")],
                )
            )
            await self._persist(ctx, session_id, messages, n_loaded, branch_id)
            raise

        await self._persist(ctx, session_id, messages, n_loaded, branch_id)

        await self._compact_after_turn(session_id, branch_id, messages)

        ans = self._final_text(messages)
        await self._deliver(
            ctx, msg, {"text": ans}, sender=self.id, output_topic=self._output_topic
        )

        return AgentRunResult(
            output=ans,
            status=status,
            tool_calls=tool_call_records,
            run_id=ctx.run_id,
        )


__all__ = ["ReActAgent"]
