"""OrchestratorAgent — delegates to sub-agents via runtime message-passing.

Each sub-agent is registered with the runtime and wrapped in a ``_DispatchTool``
that the LLM calls. The LLM decides which specialist to call via tool-calling
syntax; the tool delivers the task via ``runtime.send_message`` so crashes are
isolated from the orchestrator's own ReAct loop.

Crash recovery:
  When a subagent crashes (``AgentCrashError``), the orchestrator consults its
  ``RetryPolicy``. If the policy allows a retry, the subagent is resumed from
  its persisted history via ``agent.run(..., resume=True)``. On exhaustion the
  error is returned as a ToolExecutionResult with ``is_error=True``.

Priority / budget:
  ``sub_agents`` accepts both ``ReActAgent`` instances (wrapped at
  ``Priority.NORMAL``) and ``SubAgentConfig`` objects for per-agent priority.
  A ``SpawnBudget`` enforces the run-wide headcount cap; HIGH/CRITICAL agents
  can preempt lower-priority ones when the pool is full.

Usage::

    orchestrator = OrchestratorAgent(
        name="router",
        description="Routes queries to the right specialist",
        model=openai_client,
        runtime=runtime,
        sub_agents=[
            SubAgentConfig(code_agent, priority=Priority.HIGH),
            SubAgentConfig(research_agent, priority=Priority.NORMAL),
        ],
    )
    result = await orchestrator.run("Find all prime numbers under 100")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from ravi.kernel import (
    AgentId,
    AgentRuntime,
    AgentCrashError,
    BudgetExhaustedError,
    Priority,
    Supervision,
    TextBlock,
    Tool,
    ToolExecutionResult,
)
from ravi.kernel.stream import AgentProgress, AgentStep
from ravi.kernel.identity import HistoryRetention, HistoryRetention as _HR
from ravi.kernel.llm import LLMClient
from ravi.agents.supervision.budget import SpawnBudget
from ravi.agents.supervision.policies import RetryPolicy
from ravi.agents.resources.budget import ExecutionBudget
from ravi.agents.core.agent import (
    AgentRunResult,
    ReActAgent,
)
from ravi.agents.hooks.manager import HookEvent, HookManager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SubAgentConfig — per-agent priority + budget
# ---------------------------------------------------------------------------


@dataclass
class SubAgentConfig:
    """Configuration for one subagent in an OrchestratorAgent.

    Parameters
    ----------
    agent:
        The ReActAgent instance.
    priority:
        Budget weight for this agent. HIGH/CRITICAL agents can preempt
        NORMAL/LOW/BACKGROUND agents when the headcount pool is full.
    execution_budget:
        Optional per-agent cap (tokens, cost, turns). If None, no per-agent
        cap is applied (only the run-wide SpawnBudget headcount limit).
    retention:
        History retention policy for this subagent.
        - ``RUN`` (default) — history is cleared after each run; the subagent
          starts fresh every turn. Use for stateless specialists.
        - ``PERMANENT`` — history accumulates across runs in the same session;
          the subagent remembers everything from prior turns.
        - ``NONE`` — nothing is persisted.
    """

    agent: ReActAgent
    priority: Priority = Priority.NORMAL
    execution_budget: ExecutionBudget | None = None
    retention: "HistoryRetention" = None  # type: ignore[assignment]  resolved in __post_init__

    def __post_init__(self) -> None:
        from ravi.kernel.identity import HistoryRetention
        if self.retention is None:  # type: ignore[comparison-overlap]
            self.retention = HistoryRetention.RUN


# ---------------------------------------------------------------------------
# _DispatchTool — thin LLM-callable wrapper around runtime.send_message
# ---------------------------------------------------------------------------


class _DispatchTool:
    """Wraps a subagent as an LLM-callable tool using runtime message-passing.

    The LLM still expresses delegation as a tool call (clean, declarative).
    The actual execution goes through ``runtime.send_message`` so the
    orchestrator's ReAct loop state is never inside the subagent's call stack
    — a subagent crash raises ``AgentCrashError`` which the orchestrator can
    catch and retry via ``run(..., resume=True)``.

    Schema exposed to the LLM::

        {"input": "<instruction>", "reason": "<why delegating>"}
    """

    def __init__(
        self,
        config: SubAgentConfig,
        hooks: HookManager,
        orchestrator_id: AgentId,
        runtime: AgentRuntime,
        supervision: Supervision,
        budget: SpawnBudget,
        retry_policy: RetryPolicy,
    ) -> None:
        self._config = config
        self._agent = config.agent
        self._hooks = hooks
        self._orchestrator_id = orchestrator_id
        self._runtime = runtime
        self._supervision = supervision
        self._budget = budget
        self._retry_policy = retry_policy
        self.name = f"handoff_{self._agent.name}"
        self.description = (
            f"Delegate the current task to the '{self._agent.name}' specialist agent. "
            f"{getattr(self._agent, 'description', '')}"
        )
        self.input_schema: dict[str, object] = {
            "type": "object",
            "properties": {
                "input": {
                    "type": "string",
                    "description": (
                        f"The full instruction to send to the '{self._agent.name}' agent."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": "Why you are delegating to this agent.",
                },
            },
            "required": ["input"],
            "additionalProperties": False,
        }

    async def execute(
        self, *, input: str, reason: str = "", **_kw: Any
    ) -> ToolExecutionResult:  # noqa: A002
        logger.debug(
            "Dispatch → %s | reason: %s | input: %.80s",
            self._agent.name,
            reason,
            input,
        )
        await self._hooks.dispatch(
            HookEvent.HANDOFF,
            HandoffEventPayload(
                from_agent="orchestrator",
                to_agent=self._agent.name,
                input=input,
                reason=reason,
            ).model_dump(mode="json"),
        )
        # Emit HANDOFF on the run-scoped topic
        await self._runtime.publish_message(
            AgentProgress(
                agent_id=self._orchestrator_id,
                step=AgentStep.HANDOFF,
                content=f"→ {self._agent.name}: {reason or input[:60]}",
                run_id=self._supervision.run_id,
                parent_id=self._supervision.parent_id,
                depth=self._supervision.depth,
            ),
            sender=self._orchestrator_id,
            topic=self._supervision.progress_topic,
        )

        try:
            # Delegate via runtime message-passing (not tool call).
            # AgentCrashError propagates cleanly; orchestrator can retry.
            result: AgentRunResult = await self._runtime.send_message(
                input,
                sender=self._orchestrator_id,
                recipient=self._agent.id,
            )  # type: ignore[assignment]
            output = result.output or "(no output)"
            return ToolExecutionResult(
                content=[TextBlock(text=output)],
                is_error=result.status in ("error", "guardrail_tripped"),
            )

        except AgentCrashError as exc:
            # Consult retry policy — resume from persisted history checkpoint.
            while self._retry_policy.should_retry(self._agent.id):
                logger.warning(
                    "[orchestrator] retrying %s after crash (run_id=%s)",
                    self._agent.name,
                    exc.run_id,
                )
                try:
                    result = await self._runtime.send_message(
                        _ResumePayload(
                            run_id=exc.run_id,
                            session_id=self._supervision.session_id,
                            input=input,
                        ),
                        sender=self._orchestrator_id,
                        recipient=self._agent.id,
                    )  # type: ignore[assignment]
                    output = result.output or "(no output)"
                    return ToolExecutionResult(
                        content=[TextBlock(text=output)],
                        is_error=result.status in ("error", "guardrail_tripped"),
                    )
                except AgentCrashError as retry_exc:
                    exc = retry_exc

            logger.error(
                "[orchestrator] %s exhausted retries: %s", self._agent.name, exc
            )
            return ToolExecutionResult(
                content=[TextBlock(text=f"Agent '{self._agent.name}' failed: {exc}")],
                is_error=True,
            )




# ---------------------------------------------------------------------------
# Internal resume payload
# ---------------------------------------------------------------------------


@dataclass
class _ResumePayload:
    """Sent to on_message when the orchestrator wants to resume a crashed run."""
    run_id: str
    session_id: str
    input: str


# ---------------------------------------------------------------------------
# Handoff event payload
# ---------------------------------------------------------------------------


class HandoffEventPayload(BaseModel):
    event: str = "on_handoff"
    from_agent: str
    to_agent: str
    input: str
    reason: str


# ---------------------------------------------------------------------------
# OrchestratorAgent
# ---------------------------------------------------------------------------


class OrchestratorAgent(ReActAgent):
    """Orchestrates a set of specialist sub-agents via runtime message-passing.

    Each sub-agent is registered with the runtime when ``run()`` starts and
    wrapped in a ``_DispatchTool`` so the LLM can delegate declaratively.
    Execution goes through ``runtime.send_message`` for crash isolation.

    Parameters
    ----------
    name:           Agent identifier.
    description:    Human-readable purpose (appended to system prompt roster).
    runtime:        Actor runtime used by the orchestrator itself.
    model:          LLM client for the orchestrator's own reasoning loop.
    sub_agents:     Specialist agents (ReActAgent or SubAgentConfig).
    system:         Override the default orchestrator system prompt.
    max_iterations: Max orchestrator ReAct iterations (default 30).
    hooks:          HookManager — receives HANDOFF events on each delegation.
    extra_tools:    Additional non-handoff tools for the orchestrator loop.
    tool_timeout:   Per-tool (including handoff) timeout in seconds.
    max_agents:     Run-wide headcount cap (defaults to 50).
    """

    def __init__(
        self,
        name: str,
        runtime: AgentRuntime,
        *,
        model: LLMClient,
        sub_agents: list[ReActAgent | SubAgentConfig],
        description: str = "",
        system: str | None = None,
        max_iterations: int = 30,
        hooks: HookManager | None = None,
        extra_tools: list[Tool] | None = None,
        tool_timeout: float | None = 60.0,
        max_agents: int = 50,
        session_id: str | None = None,
    ) -> None:
        if not sub_agents:
            raise ValueError("OrchestratorAgent requires at least one sub_agent")

        self.description = description

        # Normalise: bare ReActAgent → SubAgentConfig at NORMAL priority
        configs: list[SubAgentConfig] = [
            a if isinstance(a, SubAgentConfig) else SubAgentConfig(a)
            for a in sub_agents
        ]
        self.sub_agents: list[ReActAgent] = [c.agent for c in configs]
        self._sub_configs: list[SubAgentConfig] = configs

        roster = "\n".join(
            f"  - {c.agent.name}: {getattr(c.agent, 'description', '')}" for c in configs
        )
        default_system = (
            "You are an orchestrator agent. Analyse the user's request and "
            "delegate to the most appropriate specialist agent.\n\n"
            f"Available specialists:\n{roster}\n\n"
            "You may call multiple agents in sequence. Synthesise their outputs "
            "into a coherent final answer."
        )

        _hooks = hooks or HookManager()
        orchestrator_id = AgentId(type="assistant", key=name)

        # Root supervision — generates the shared run_id and carries the session_id.
        # session_id is the conversation thread; stable across runs on this instance.
        root_supervision = Supervision.root(
            orchestrator_id,
            session_id=session_id,
            max_agents=max_agents,
            retention=HistoryRetention.PERMANENT,
        )

        # SpawnBudget enforces max_agents with priority-based preemption.
        self._spawn_budget = SpawnBudget(root_supervision)

        # RetryPolicy for crash-resume logic.
        self._retry_policy = RetryPolicy(max_retries=2)

        # Stamp each subagent with supervision + budget, then build dispatch tools.
        dispatch_tools: list[_DispatchTool] = []
        for cfg in configs:
            child_sv = root_supervision.spawn_child(
                orchestrator_id,
                retention=cfg.retention,
                priority=cfg.priority,
            )
            cfg.agent.supervision = child_sv
            cfg.agent.spawn_budget = self._spawn_budget
            if cfg.execution_budget is not None:
                cfg.agent.execution_budget = cfg.execution_budget

            dispatch_tools.append(
                _DispatchTool(
                    cfg,
                    _hooks,
                    orchestrator_id,
                    runtime,
                    root_supervision,
                    self._spawn_budget,
                    self._retry_policy,
                )
            )

        all_tools: list[Tool] = [*dispatch_tools, *(extra_tools or [])]

        super().__init__(
            name,
            runtime,
            model=model,
            tools=all_tools,
            system_instructions=system or default_system,
            max_iterations=max_iterations,
            tool_timeout=tool_timeout,
            hooks=_hooks,
            supervision=root_supervision,
        )

    # -- Actor entry point (override to handle resume payloads) ---------------

    async def on_message(self, ctx: Any, payload: object) -> object:
        """Actor runtime entry point. Handles both str tasks and _ResumePayload."""
        if isinstance(payload, _ResumePayload):
            return await self.run(
                payload.input,
                resume=True,
                run_id=payload.run_id,
                session_id=payload.session_id,
            )
        return await super().on_message(ctx, payload)

    # -- Override _react() to handle subagents ------------------------------

    async def _react(
        self,
        input_text: str,
        *,
        session_id: str | None = None,
        resume: bool = False,
        run_id: str | None = None,
        stream: bool = False,
    ) -> Any:
        # Each call gets a fresh run_id for execution scope (budget/progress)
        # while all calls on this instance share the same session_id.
        for cfg in self._sub_configs:
            self._retry_policy.reset(cfg.agent.id)
            await self.runtime.register(cfg.agent.id, cfg.agent.on_message)
            try:
                self._spawn_budget.acquire(cfg.agent.id, cfg.priority)
            except BudgetExhaustedError as exc:
                raise ValueError(
                    f"Cannot acquire budget for subagent '{cfg.agent.name}': {exc}"
                ) from exc

        try:
            async for event in super()._react(
                input_text,
                session_id=session_id,
                resume=resume,
                run_id=run_id,
                stream=stream,
            ):
                yield event
        finally:
            for cfg in self._sub_configs:
                self._spawn_budget.release(cfg.agent.id)
                await self.runtime.unregister(cfg.agent.id)

            # Clear history for RUN-retention subagents so next turn starts fresh.
            sv = self.supervision
            if sv is not None:
                for cfg in self._sub_configs:
                    if cfg.retention == _HR.RUN:
                        try:
                            await cfg.agent.history.clear(
                                cfg.agent.id,
                                session_id=sv.session_id,
                            )
                        except Exception:
                            pass  # best-effort cleanup

    # -- Dynamic reprioritization -------------------------------------------

    def reprioritize(self, agent_name: str, new_priority: Priority) -> None:
        """Change a subagent's priority mid-run.

        Delegates to ``SpawnBudget.reprioritize``. Thread-safe.
        If demoted below NORMAL and pool is full, the agent is automatically
        paused (it will stop making LLM calls at the next cooperative check).
        If promoted, the pause is lifted.

        Callable from outside (e.g. an API endpoint) while the orchestrator
        is running.
        """
        for agent in self.sub_agents:
            if agent.name == agent_name:
                self._spawn_budget.reprioritize(agent.id, new_priority)
                return
        raise ValueError(f"No subagent named '{agent_name}'")
