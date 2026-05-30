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
from ravi.fabric.llm import LLMClient
from ravi.reasoning.agents.assistant.agent import (
    AgentRunResult,
    AssistantAgent,
)
from ravi.reasoning.hooks.manager import HookEvent, HookManager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# _HandoffTool — wraps one sub-agent as a Tool Protocol class
# ---------------------------------------------------------------------------


class _HandoffTool:
    """Wraps an AssistantAgent as a tool callable by the orchestrator's LLM.

    Schema exposed to the LLM::

        {"input": "<instruction>", "reason": "<why delegating>"}
    """

    def __init__(self, agent: AssistantAgent) -> None:
        self._agent = agent
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

    async def execute(self, *, input: str, reason: str = "", **_kw: Any) -> ToolExecutionResult:  # noqa: A002
        logger.debug(
            "Handoff → %s | reason: %s | input: %.80s",
            self._agent.name,
            reason,
            input,
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

        # Build handoff tools
        handoff_tools: list[_HandoffTool] = [_HandoffTool(a) for a in sub_agents]
        all_tools: list[Tool] = [*handoff_tools, *(extra_tools or [])]

        # Default system prompt lists the available specialists
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
        super().__init__(
            name,
            runtime,
            model=model,
            tools=all_tools,
            system=system or default_system,
            max_iterations=max_iterations,
            tool_timeout=tool_timeout,
            hooks=_hooks,
        )

        # Patch hooks to emit HANDOFF events on each delegation
        self._patch_handoff_hooks()

    # -- Hook patching -------------------------------------------------------

    def _patch_handoff_hooks(self) -> None:
        """Wrap the hook dispatcher to emit HANDOFF events on tool calls."""
        original_dispatch = self.hooks.dispatch

        async def _patched(event: HookEvent, payload: dict[str, Any]) -> None:
            if event == HookEvent.TOOL_START:
                tool_name = payload.get("tool", "")
                if isinstance(tool_name, str) and tool_name.startswith("handoff_"):
                    agent_name = tool_name[len("handoff_"):]
                    args = payload.get("args") or {}
                    await original_dispatch(
                        HookEvent.HANDOFF,
                        HandoffEventPayload(
                            from_agent=self.name,
                            to_agent=agent_name,
                            input=str(args.get("input", "")),
                            reason=str(args.get("reason", "")),
                        ).model_dump(mode="json"),
                    )
            await original_dispatch(event, payload)

        self.hooks.dispatch = _patched  # type: ignore[method-assign]
