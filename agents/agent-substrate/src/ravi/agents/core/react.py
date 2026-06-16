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
    ) -> None:
        self.id = AgentId(type="agent", key=name)
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

    @property
    def history(self) -> HistoryProvider:
        return self._context.history

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        for msg in inbox:
            ctx.check()
            await self._handle_message(ctx, msg)

    async def _handle_message(self, ctx: RunContext, msg: Message) -> None:
        session_id = msg.correlation_id or ctx.run_id

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
        options = GenerationOptions(
            system_instructions=self._system_instructions,
            tools=tool_list or None,
        )

        for _ in range(self._max_iterations):
            ctx.check()
            if self.hooks:
                await self.hooks.dispatch(
                    HookEvent.LLM_START, {"agent_name": self.name, "run_id": ctx.run_id}
                )
            resp = await ctx.llm(messages, options=options)
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
