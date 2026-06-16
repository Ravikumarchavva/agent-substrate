"""OrchestratorAgent — spawns sub-agents and awaits their results.

The LLM decides which sub-agent to delegate to by calling a tool whose name
matches the sub-agent's routing key.  The orchestrator spawns the sub-agent,
waits for the reply (or timeout / failure), and loops until the task is
complete or the budget is exhausted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ravi.kernel.core.content import (
    ChatMessage,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from ravi.kernel.core.identity import AgentId
from ravi.kernel.llm.llm import GenerationOptions
from ravi.kernel.messaging.message import ChatPayload, DataPayload, Message
from ravi.kernel.tools.tools import ToolExecutionResult

from ravi.kernel.agent.supervision import Priority, SpawnBudget
from ravi.agents.context.context import ContextConfig
from ravi.agents.supervision.budget import SpawnTracker
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
    from ravi.kernel.runtime.agent import Agent
    from ravi.kernel.storage.history import HistoryProvider


@dataclass(frozen=True)
class _DelegateTool:
    """Minimal Tool-protocol stub for sub-agent delegation.

    Synthesized so that ``GenerationOptions(tools=...)`` and ``spec_of()``
    encode the routing correctly.  ``execute()`` is never called — the
    orchestrator handles dispatch via ``ctx.spawn`` + ``ctx.ask``.
    """

    name: str
    description: str
    input_schema: dict[str, object] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The task to delegate"},
            },
            "required": ["task"],
        }
    )

    async def execute(self, *, ctx: Any = None, **kwargs: Any) -> ToolExecutionResult:
        raise RuntimeError(
            f"_DelegateTool({self.name}).execute() should never be called"
        )


@dataclass
class SubAgentConfig:
    """Declares one sub-agent available to this orchestrator."""

    agent: Agent
    description: str = ""
    ask_timeout: float = 120.0
    priority: Priority = Priority.NORMAL


class OrchestratorAgent:
    """General-purpose orchestrator that spawns sub-agents via the runtime."""

    def __init__(
        self,
        name: str,
        *,
        model: LLMClient | None = None,
        sub_agents: list[SubAgentConfig] | None = None,
        context: ContextConfig | None = None,
        system_instructions: str = (
            "You are an orchestrator agent.  "
            "Break down tasks and delegate to sub-agents via their tools.  "
            "Return the final consolidated answer when done."
        ),
        max_iterations: int = 10,
        spawn_budget: SpawnBudget | None = None,
    ) -> None:
        self.id = AgentId(type="agent", key=name)
        self.name = name
        self.model = model
        self.tools = None  # built dynamically from sub-agents
        self._sub_agents = sub_agents or []
        self._context = context or ContextConfig.default()
        self._system_instructions = system_instructions
        self._max_iterations = max_iterations
        self._spawn_budget = spawn_budget or SpawnBudget()

    @property
    def history(self) -> HistoryProvider:
        return self._context.history

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        for msg in inbox:
            ctx.check()
            await self._handle_message(ctx, msg)

    async def _handle_message(self, ctx: RunContext, msg: Message) -> None:
        session_id = msg.correlation_id or ctx.run_id
        spawn_tracker = SpawnTracker(self._spawn_budget)

        history_messages = await load_history(self._context, self.id, session_id)
        user_turn = message_to_chat(msg)
        messages: list[ChatMessage] = history_messages + [user_turn]
        n_loaded = len(history_messages)

        tools = self._build_tools()
        options = GenerationOptions(
            system_instructions=self._system_instructions,
            tools=tools or None,
        )

        for _ in range(self._max_iterations):
            ctx.check()
            resp = await ctx.llm(messages, options=options)

            messages.append(ChatMessage(role=Role.ASSISTANT, content=resp.content))

            dispatches = [b for b in resp.content if isinstance(b, ToolUseBlock)]
            if not dispatches:
                break

            results: list[ToolResultBlock] = []
            for dispatch in dispatches:
                ctx.check()
                cfg = self._find_sub_agent_config(dispatch.tool_name)
                if cfg is None:
                    results.append(ToolResultBlock(
                        call_id=dispatch.call_id,
                        content=[TextBlock(text=f"Unknown sub-agent: {dispatch.tool_name}")],
                        is_error=True,
                    ))
                    continue

                spawn_tracker.acquire(cfg.agent.id, priority=cfg.priority)
                task_text = dispatch.arguments.get("task", str(dispatch.arguments))
                boot_msg = Message(
                    target=cfg.agent.id,
                    sender=self.id,
                    payload=ChatPayload(
                        message=ChatMessage(
                            role=Role.USER,
                            content=[TextBlock(text=str(task_text))],
                        )
                    ),
                    correlation_id=session_id,
                )
                try:
                    handle = await ctx.spawn(cfg.agent.id, boot=boot_msg)
                    outcome = await ctx.ask(handle, boot_msg, timeout=cfg.ask_timeout)
                finally:
                    spawn_tracker.release(cfg.agent.id)

                if outcome.kind == "replied" and outcome.result:
                    out = outcome.result.output
                    text = (
                        out.data.get("text", str(out.data))
                        if isinstance(out, DataPayload)
                        else str(out)
                    )
                    results.append(ToolResultBlock(
                        call_id=dispatch.call_id,
                        content=[TextBlock(text=text)],
                        is_error=False,
                    ))
                else:
                    results.append(ToolResultBlock(
                        call_id=dispatch.call_id,
                        content=[TextBlock(text=f"Sub-agent {dispatch.tool_name}: {outcome.kind}")],
                        is_error=True,
                    ))

            messages.append(ChatMessage(role=Role.TOOL, content=results))  # type: ignore[arg-type]
        else:
            from ravi.kernel.core.errors import BudgetExhaustedError
            raise BudgetExhaustedError(f"Agent reached max iterations limit ({self._max_iterations})")

        new_turns = messages[n_loaded:]
        await persist_turns(self._context, self.id, session_id, ctx.run_id, new_turns)

        ans = final_text(messages)
        await deliver(ctx, msg, {"text": ans}, sender=self.id)

    def _build_tools(self) -> list[_DelegateTool]:
        return [
            _DelegateTool(
                name=f"handoff_{cfg.agent.id.key}",
                description=cfg.description or f"Delegate to the {cfg.agent.id.key} sub-agent",
            )
            for cfg in self._sub_agents
        ]

    def _find_sub_agent_config(self, name: str) -> SubAgentConfig | None:
        for cfg in self._sub_agents:
            if f"handoff_{cfg.agent.id.key}" == name or cfg.agent.id.key == name:
                return cfg
        return None


__all__ = ["SubAgentConfig", "OrchestratorAgent"]
