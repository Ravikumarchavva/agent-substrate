"""OrchestratorAgent — spawns sub-agents and awaits their results.

The LLM decides which sub-agent to delegate to by calling a tool whose name
matches the sub-agent's routing key.  The orchestrator spawns the sub-agent,
waits for the reply (or timeout / failure), and loops until the task is
complete or the budget is exhausted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from substrate.kernel.core.content import (
    ChatMessage,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from substrate.kernel.core.identity import AgentId
from substrate.kernel.llm.llm import GenerationOptions
from substrate.kernel.messaging.message import ChatPayload, DataPayload, Message
from substrate.kernel.tools import AnyTool
from substrate.kernel.tools.tools import ToolExecutionResult

from substrate.kernel.agent.supervision import Priority, SpawnBudget
from substrate.agents.context.context import ContextConfig
from substrate.agents.supervision.budget import SpawnTracker
from substrate.agents.storage.tasks import (
    current_agent_id as _task_agent_id,
    current_agent_label as _task_agent_label,
    current_parent_agent_id as _task_parent_agent_id,
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
    from substrate.agents.runtime.context import Agent, RunContext
    from substrate.kernel.llm.llm import LLMClient
    from substrate.kernel.storage.history import HistoryProvider


@dataclass
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
        session_id: str | None = None,
    ) -> None:
        self.id = AgentId(
            type="agent", key=f"{name}-{session_id}" if session_id else name
        )
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
        _task_agent_id.set(str(self.id))
        _task_agent_label.set(self.name)
        _task_parent_agent_id.set(None)  # orchestrator is the root
        for msg in inbox:
            ctx.check()
            await self._handle_message(ctx, msg)

    async def _handle_message(self, ctx: RunContext, msg: Message) -> None:
        session_id = msg.correlation_id or ctx.run_id
        # See ReActAgent._handle_message for why this must be stamped here
        # (inside the Worker task) rather than upstream.
        _task_user_id.set(msg.metadata.get("user_id") or None)
        spawn_tracker = SpawnTracker(self._spawn_budget)

        history_messages = await load_history(self._context, self.id, session_id)
        user_turn = message_to_chat(msg)
        await log_user_message(ctx, msg, user_turn)
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
                    results.append(
                        ToolResultBlock(
                            call_id=dispatch.call_id,
                            content=[
                                TextBlock(
                                    text=f"Unknown sub-agent: {dispatch.tool_name}"
                                )
                            ],
                            is_error=True,
                        )
                    )
                    continue

                spawn_tracker.acquire(cfg.agent.id, priority=cfg.priority)
                task_text = dispatch.arguments.get("task", str(dispatch.arguments))
                # Surface subagent lifecycle on the orchestrator's own run log so
                # console / UIs can render a live subagent progress tree. The
                # subagent itself runs under a separate run_id we don't tail here.
                await ctx._log(
                    "subagent.start",
                    {
                        "agent": cfg.agent.id.key,
                        "parent": self.id.key,
                        "task": str(task_text)[:200],
                    },
                )
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
                    # The subagent Worker runs in its own ContextVar context, so
                    # pass the parent id explicitly; the subagent stamps it as
                    # current_parent_agent_id so its board nests under this one.
                    # user_id rides along the same way so a spawned subagent's
                    # code-interpreter calls still resolve to the caller's
                    # workspace subPath.
                    metadata={
                        "parent_agent_id": str(self.id),
                        "user_id": _task_user_id.get(),
                    },
                )
                try:
                    handle = await ctx.spawn(cfg.agent.id, boot=boot_msg)
                    outcome = await ctx.ask(handle, boot_msg, timeout=cfg.ask_timeout)
                finally:
                    spawn_tracker.release(cfg.agent.id)

                await ctx._log(
                    "subagent.done",
                    {
                        "agent": cfg.agent.id.key,
                        "parent": self.id.key,
                        "ok": outcome.kind == "replied",
                    },
                )

                if outcome.kind == "replied" and outcome.result:
                    out = outcome.result.output
                    text = (
                        out.data.get("text", str(out.data))
                        if isinstance(out, DataPayload)
                        else str(out)
                    )
                    results.append(
                        ToolResultBlock(
                            call_id=dispatch.call_id,
                            content=[TextBlock(text=text)],
                            is_error=False,
                        )
                    )
                else:
                    results.append(
                        ToolResultBlock(
                            call_id=dispatch.call_id,
                            content=[
                                TextBlock(
                                    text=f"Sub-agent {dispatch.tool_name}: {outcome.kind}"
                                )
                            ],
                            is_error=True,
                        )
                    )

            messages.append(ChatMessage(role=Role.TOOL, content=results))  # type: ignore[arg-type]
        else:
            from substrate.kernel.core.errors import BudgetExhaustedError

            raise BudgetExhaustedError(
                f"Agent reached max iterations limit ({self._max_iterations})"
            )

        new_turns = messages[n_loaded:]
        await persist_turns(self._context, self.id, session_id, ctx.run_id, new_turns)

        ans = final_text(messages)
        await deliver(ctx, msg, {"text": ans}, sender=self.id)

    def _build_tools(self) -> list[AnyTool]:
        tools: list[AnyTool] = [
            _DelegateTool(
                name=f"handoff_{cfg.agent.id.key}",
                description=cfg.description
                or f"Delegate to the {cfg.agent.id.key} sub-agent",
            )
            for cfg in self._sub_agents
        ]
        return tools

    def _find_sub_agent_config(self, name: str) -> SubAgentConfig | None:
        for cfg in self._sub_agents:
            if f"handoff_{cfg.agent.id.key}" == name or cfg.agent.id.key == name:
                return cfg
        return None


__all__ = ["SubAgentConfig", "OrchestratorAgent"]
