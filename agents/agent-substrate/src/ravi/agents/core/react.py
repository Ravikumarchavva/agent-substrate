"""ReActAgent — ReAct reasoning loop on the durable kernel."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ravi.kernel.core.content import (
    ChatMessage,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from ravi.kernel.core.identity import AgentId, TopicId
from ravi.kernel.llm.llm import GenerationOptions
from ravi.kernel.messaging.message import Message
from ravi.kernel.storage.history import HistoryProvider
from ravi.kernel.tools import ToolRegistry
from ravi.kernel.tools.approval import ApprovalHandler
from ravi.kernel.tools.tools import ToolRisk

from ravi.agents.context.context import ContextConfig
from ravi.agents.resources.budget import ExecutionTracker
from ravi.agents.hooks.manager import HookEvent, HookManager
from ravi.agents.middleware.pipeline import MiddlewarePipeline
from ravi.agents.storage.tasks import (
    current_agent_id as _task_agent_id,
    current_agent_label as _task_agent_label,
    current_parent_agent_id as _task_parent_agent_id,
    current_thread_id as _task_thread_id,
)
from ravi.agents.core._loop import (
    deliver,
    final_text,
    load_history,
    message_to_chat,
    persist_turns,
)

if TYPE_CHECKING:
    from ravi.agents.runtime.context import RunContext
    from ravi.kernel.llm.llm import LLMClient


class ReActAgent:
    """ReAct loop agent implementing the Agent protocol.

    ``model``, ``tools``, ``approval_handler``, and ``approval_required_risk``
    are read by the Worker when building the per-run RunContext.
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
            from ravi.agents.tools.toolbox import Toolbox

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
        self.middleware = middleware
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

        history_messages = await load_history(self._context, self.id, session_id)
        user_turn = message_to_chat(msg)
        messages: list[ChatMessage] = history_messages + [user_turn]

        await self._react_loop(ctx, msg, session_id, messages, len(history_messages))

    async def _react_loop(
        self,
        ctx: RunContext,
        msg: Message,
        session_id: str,
        messages: list[ChatMessage],
        n_loaded: int,
    ) -> None:
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

        for _ in range(self._max_iterations):
            ctx.check()
            if self.hooks:
                await self.hooks.dispatch(
                    HookEvent.LLM_START, {"agent_name": self.name, "run_id": ctx.run_id}
                )
            # Compact before each LLM call so tool results don't inflate the
            # context unboundedly across iterations.  We compact a *view* of
            # messages here and keep the full list intact for persistence.
            llm_messages = await self._context.pipeline.compact(messages)
            resp = await ctx.llm(llm_messages, options=options)
            # Drop the forced tool_choice after the first call so subsequent
            # iterations can freely choose to respond with text or more tools.
            options = base_options
            if self.hooks:
                await self.hooks.dispatch(
                    HookEvent.LLM_END,
                    {
                        "agent_name": self.name,
                        "run_id": ctx.run_id,
                        "usage": resp.usage,
                    },
                )

            if self._execution_budget is not None:
                self._execution_budget.consume(
                    tokens=resp.usage.total_tokens if resp.usage else 0,
                    turns=1,
                )

            assistant_turn = ChatMessage(role=Role.ASSISTANT, content=resp.content)
            messages.append(assistant_turn)

            tool_calls = [b for b in resp.content if isinstance(b, ToolUseBlock)]
            if not tool_calls:
                break

            results: list[ToolResultBlock] = []
            for tc in tool_calls:
                ctx.check()
                inv_result = await ctx.tool(tc.tool_name, **tc.arguments)
                results.append(
                    ToolResultBlock(
                        call_id=tc.call_id,
                        content=[TextBlock(text=inv_result.text or "")],
                        is_error=inv_result.status != "ok",
                    )
                )

            messages.append(ChatMessage(role=Role.TOOL, content=results))  # type: ignore[arg-type]
        else:
            from ravi.kernel.core.errors import BudgetExhaustedError

            raise BudgetExhaustedError(
                f"Agent reached max iterations limit ({self._max_iterations})"
            )

        new_turns = messages[n_loaded:]
        await persist_turns(self._context, self.id, session_id, ctx.run_id, new_turns)

        ans = final_text(messages)
        await deliver(
            ctx, msg, {"text": ans}, sender=self.id, output_topic=self._output_topic
        )


__all__ = ["ReActAgent"]
