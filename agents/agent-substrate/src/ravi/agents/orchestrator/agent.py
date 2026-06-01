"""OrchestratorAgent — delegates to sub-agents via tool-calling.

Each sub-agent is wrapped in a ``_HandoffTool`` and registered with an
``AssistantAgent`` loop.  The LLM calls the appropriate handoff tool; the
tool runs the target agent and returns its output as a ``ToolResultBlock``.

Usage::

    orchestrator = OrchestratorAgent(
        name="router",
        description="Routes queries to the right specialist",
        model=openai_client,
        runtime=runtime,
        sub_agents=[code_agent, research_agent],
    )
    result = await orchestrator.run("Find all prime numbers under 100")
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from ravi.kernel import (
    AgentRuntime,
    TextBlock,
    Tool,
    ToolExecutionResult,
)
from ravi.kernel.llm import LLMClient
from ravi.agents.assistant.agent import (
    AgentRunResult,
    AssistantAgent,
)
from ravi.agents.hooks.manager import HookEvent, HookManager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# _HandoffTool — wraps one sub-agent as a Tool Protocol class
# ---------------------------------------------------------------------------


class _HandoffTool:
    """Wraps an AssistantAgent as a tool callable by the orchestrator's LLM.

    Schema exposed to the LLM::

        {"input": "<instruction>", "reason": "<why delegating>"}
    """

    def __init__(self, agent: AssistantAgent, hooks: HookManager) -> None:
        self._agent = agent
        self._hooks = hooks
        self.name = f"handoff_{agent.name}"
        self.description = (
            f"Delegate the current task to the '{agent.name}' specialist agent. "
            f"{getattr(agent, 'description', '')}"
        )
        self.input_schema: dict[str, object] = {
            "type": "object",
            "properties": {
                "input": {
                    "type": "string",
                    "description": (
                        f"The full instruction to send to the '{agent.name}' agent."
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
            "Handoff → %s | reason: %s | input: %.80s",
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
        result: AgentRunResult = await self._agent.run(input)
        output = result.output or "(no output)"
        return ToolExecutionResult(
            content=[TextBlock(text=output)],
            is_error=result.status in ("error", "guardrail_tripped"),
        )


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


class OrchestratorAgent(AssistantAgent):
    """Orchestrates a set of specialist sub-agents via handoff tools.

    Each sub-agent is automatically wrapped in a ``_HandoffTool`` and
    registered alongside any ``extra_tools``.  The LLM decides which agent
    to call and can chain multiple calls in sequence.

    Parameters
    ----------
    name:           Agent identifier.
    description:    Human-readable purpose (appended to system prompt roster).
    runtime:        Actor runtime used by the orchestrator itself.
    model:          LLM client for the orchestrator's own reasoning loop.
    sub_agents:     Specialist AssistantAgents to delegate to.
    system:         Override the default orchestrator system prompt.
    max_iterations: Max orchestrator ReAct iterations (default 30).
    hooks:          HookManager — receives HANDOFF events on each delegation.
    extra_tools:    Additional non-handoff tools for the orchestrator loop.
    tool_timeout:   Per-tool (including handoff) timeout in seconds.
    """

    def __init__(
        self,
        name: str,
        runtime: AgentRuntime,
        *,
        model: LLMClient,
        sub_agents: list[AssistantAgent],
        description: str = "",
        system: str | None = None,
        max_iterations: int = 30,
        hooks: HookManager | None = None,
        extra_tools: list[Tool] | None = None,
        tool_timeout: float | None = 60.0,
    ) -> None:
        if not sub_agents:
            raise ValueError("OrchestratorAgent requires at least one sub_agent")

        self.description = description
        self.sub_agents = sub_agents

        roster = "\n".join(
            f"  - {a.name}: {getattr(a, 'description', '')}" for a in sub_agents
        )
        default_system = (
            "You are an orchestrator agent. Analyse the user's request and "
            "delegate to the most appropriate specialist agent.\n\n"
            f"Available specialists:\n{roster}\n\n"
            "You may call multiple agents in sequence. Synthesise their outputs "
            "into a coherent final answer."
        )

        _hooks = hooks or HookManager()
        handoff_tools = [_HandoffTool(a, _hooks) for a in sub_agents]
        all_tools: list[Tool] = [*handoff_tools, *(extra_tools or [])]

        super().__init__(
            name,
            runtime,
            model=model,
            tools=all_tools,
            system_instructions=system or default_system,
            max_iterations=max_iterations,
            tool_timeout=tool_timeout,
            hooks=_hooks,
        )
