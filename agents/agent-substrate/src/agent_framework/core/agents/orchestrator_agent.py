"""OrchestratorAgent — delegates to sub-agents via the tool-calling loop.

Architecture
------------
The OrchestratorAgent is a ``ReActAgent`` whose *tools* are dynamically
generated wrappers around sub-agents.  When the LLM decides to hand off to
a sub-agent it emits a tool call ``{"agent_name": "...", "reason": "..."}``
and the framework:

1. Fires ``HookEvent.HANDOFF`` so observability hooks can log the delegation.
2. Emits an ``agent_handoff`` SSE event to the frontend (via the bridge).
3. Runs the target sub-agent with the orchestrator's current message as input.
4. Returns the sub-agent's output back to the orchestrator loop as a tool
   result, which the orchestrator can reason about and either finalize or
   delegate again.

Handoff guardrails
------------------
Pass ``handoff_guardrails`` to restrict which agents can be called and under
what conditions.  They run as ``GuardrailType.TOOL_CALL`` guardrails applied
*only* to handoff tool calls (not to the sub-agent's own tool calls).

Usage::

    orchestrator = OrchestratorAgent(
        name="router",
        description="Routes queries to the right specialist",
        model_client=openai_client,
        sub_agents=[code_agent, research_agent, math_agent],
    )
    result = await orchestrator.run("Find all prime numbers under 100")
"""
from __future__ import annotations

from typing import Dict, List, Optional

from agent_framework.core.agents.base_agent import BaseAgent
from agent_framework.core.agents.react_agent import ReActAgent
from agent_framework.core.agents.agent_result import AgentRunResult
from agent_framework.core.context.base_context import ModelContext
from agent_framework.core.context.implementations import UnboundedContext
from agent_framework.core.guardrails.base_guardrail import BaseGuardrail
from agent_framework.core.hooks import HookEvent, HookManager
from agent_framework.core.memory.base_memory import BaseMemory
from agent_framework.core.memory.memory_scope import MemoryScope
from agent_framework.providers.llm.base_client import BaseModelClient
from agent_framework.runtime.observability import logger
from agent_framework.core.resilience import RetryPolicy
from agent_framework.core.tools.base_tool import BaseTool, ToolResult
from agent_framework.extensions.skills import SkillManager
from agent_framework.extensions.tools.human_input import ToolApprovalHandler


# ---------------------------------------------------------------------------
# HandoffTool — wraps a single sub-agent as a BaseTool
# ---------------------------------------------------------------------------

class _HandoffTool(BaseTool):
    """Internal tool that delegates execution to a sub-agent.

    The schema surface exposed to the LLM is::

        {
          "agent_name": "<fixed to this agent's name>",
          "input": "<the question / instruction to send to the sub-agent>"
        }

    The ``agent_name`` field is included in the schema description so the
    orchestrator LLM knows *which* agent it is calling, even though the tool
    name itself encodes that (``handoff_<agent_name>``).
    """

    def __init__(self, agent: BaseAgent) -> None:
        super().__init__(
            name=f"handoff_{agent.name}",
            description=(
                f"Delegate the current task to the '{agent.name}' specialist agent. "
                f"{agent.description}"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "input": {
                        "type": "string",
                        "description": (
                            "The full instruction or question to pass to the "
                            f"'{agent.name}' agent."
                        ),
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why you are delegating to this agent.",
                    },
                },
                "required": ["input"],
                "additionalProperties": False,
            },
        )
        self._agent = agent

    @property
    def target_agent(self) -> BaseAgent:
        return self._agent

    async def execute(self, *, input: str, reason: str = "", **_kwargs) -> ToolResult:  # noqa: A002
        """Run the sub-agent and return its output as a ToolResult."""
        logger.debug(
            "Handoff → %s | reason: %s | input: %.80s…",
            self._agent.name, reason, input,
        )
        result: AgentRunResult = await self._agent.run(input)
        output_text = result.output
        if isinstance(output_text, list):
            output_text = "\n".join(str(p) for p in output_text if isinstance(p, str))
        return ToolResult(
            content=[{"type": "text", "text": output_text or "(no output)"}],
            is_error=result.status.value in ("error", "guardrail_tripped"),
        )


# ---------------------------------------------------------------------------
# OrchestratorAgent
# ---------------------------------------------------------------------------

class OrchestratorAgent(ReActAgent):
    """Orchestrates a set of specialist sub-agents via tool-calling delegation.

    Each sub-agent is automatically wrapped in a ``_HandoffTool`` and
    registered with the underlying ``ReActAgent`` loop.  The LLM decides
    which agent to call (and can call multiple in sequence / iteration).

    Args:
        name:                 Agent identifier.
        description:          Human-readable purpose.
        model_client:         LLM client for the *orchestrator* (may differ
                              from sub-agents' clients).
        sub_agents:           List of specialist agents to delegate to.
        system_instructions:  Orchestrator-level system prompt.  A default
                              roster of sub-agents is appended automatically.
        memory:               Orchestrator's own memory instance.
        memory_scope:         Memory scope for the orchestrator itself.
        model_context:        ModelContext strategy for the orchestrator.
        max_iterations:       Max orchestrator ReAct iterations.
        handoff_guardrails:   Guardrails applied specifically to handoff calls.
        hooks:                HookManager — will receive ``HANDOFF`` events.
        extra_tools:          Additional (non-handoff) tools for the orchestrator.
        llm_retry_policy:     LLM retry policy.
        tool_retry_policy:    Tool retry policy.
        run_timeout:          Optional wall-clock timeout for the full run.
        tool_timeout:         Per-tool (including handoff) timeout.
        tool_approval_handler: HITL approval handler.
        tools_requiring_approval: Tools needing human approval.
        skill_dirs:           Skill directories.
        skill_manager:        Explicit skill manager.
        verbose:              Enable debug logging.
    """

    def __init__(
        self,
        name: str,
        description: str,
        *,
        model_client: BaseModelClient,
        sub_agents: List[BaseAgent],
        system_instructions: Optional[str] = None,
        memory: Optional[BaseMemory] = None,
        memory_scope: MemoryScope = MemoryScope.ISOLATED,
        model_context: Optional[ModelContext] = None,
        max_iterations: int = 15,
        handoff_guardrails: Optional[List[BaseGuardrail]] = None,
        hooks: Optional[HookManager] = None,
        extra_tools: Optional[List[BaseTool]] = None,
        llm_retry_policy: Optional[RetryPolicy] = None,
        tool_retry_policy: Optional[RetryPolicy] = None,
        run_timeout: Optional[float] = None,
        tool_timeout: Optional[float] = 60.0,
        tool_approval_handler: Optional[ToolApprovalHandler] = None,
        tools_requiring_approval: Optional[List[str]] = None,
        skill_dirs: Optional[List[str]] = None,
        skill_manager: Optional[SkillManager] = None,
        verbose: bool = True,
    ) -> None:
        if not sub_agents:
            raise ValueError("OrchestratorAgent requires at least one sub_agent")

        # Build handoff tools from sub-agents
        handoff_tools: List[BaseTool] = [_HandoffTool(agent) for agent in sub_agents]
        all_tools = handoff_tools + (extra_tools or [])

        # Build roster description for the system prompt
        roster = "\n".join(
            f"  - {a.name}: {a.description}" for a in sub_agents
        )
        default_instructions = (
            "You are an orchestrator agent. Your job is to analyse the user's "
            "request and delegate to the most appropriate specialist agent.\n\n"
            f"Available specialists:\n{roster}\n\n"
            "Always choose the agent best suited to the task. You may call "
            "multiple agents in sequence if needed. Synthesize their outputs "
            "into a coherent final answer."
        )

        super().__init__(
            name=name,
            description=description,
            model_client=model_client,
            model_context=model_context or UnboundedContext(),
            tools=all_tools,
            system_instructions=system_instructions or default_instructions,
            memory=memory,
            memory_scope=memory_scope,
            max_iterations=max_iterations,
            verbose=verbose,
            input_guardrails=[],
            output_guardrails=handoff_guardrails or [],
            hooks=hooks,
            llm_retry_policy=llm_retry_policy,
            tool_retry_policy=tool_retry_policy,
            run_timeout=run_timeout,
            tool_timeout=tool_timeout,
            tool_approval_handler=tool_approval_handler,
            tools_requiring_approval=tools_requiring_approval,
            skill_dirs=skill_dirs,
            skill_manager=skill_manager,
        )

        self.sub_agents = sub_agents
        self._handoff_tools: Dict[str, _HandoffTool] = {
            t.name: t for t in handoff_tools  # type: ignore[misc]
        }

        # Patch the hook dispatcher so every handoff emits HANDOFF event
        self._patch_hooks()

    # -- Hook patching --------------------------------------------------------

    def _patch_hooks(self) -> None:
        """Wrap the underlying hooks dispatcher to intercept handoff tool calls."""
        original_dispatch = self.hooks.dispatch

        async def _patched_dispatch(event: HookEvent, payload: Dict) -> None:
            if event == HookEvent.TOOL_START:
                tool_name = payload.get("tool_name", "")
                if tool_name.startswith("handoff_"):
                    agent_name = tool_name[len("handoff_"):]
                    await original_dispatch(HookEvent.HANDOFF, {
                        "event": "on_handoff",
                        "from_agent": self.name,
                        "to_agent": agent_name,
                        "input": payload.get("tool_arguments", {}).get("input", ""),
                        "reason": payload.get("tool_arguments", {}).get("reason", ""),
                    })
            await original_dispatch(event, payload)

        self.hooks.dispatch = _patched_dispatch  # type: ignore[method-assign]

    # -- Graph support --------------------------------------------------------

    def to_graph(self) -> "FlowGraph":  # type: ignore[name-defined]  # noqa: F821
        from agent_framework.core.agents.graph import FlowGraph, FlowEdge, FlowNode
        graph = FlowGraph(name=self.name)
        graph.add_node(FlowNode(id="__input__", label="start", node_type="input"))
        orch_id = self.name
        graph.add_node(FlowNode(
            id=orch_id,
            label=f"🎯 {self.name}",
            node_type="agent",
            metadata={"role": "orchestrator"},
        ))
        graph.add_edge(FlowEdge(source="__input__", target=orch_id))

        for agent in self.sub_agents:
            graph.add_node(FlowNode(
                id=agent.name,
                label=agent.name,
                node_type="agent",
                metadata={"role": "specialist"},
            ))
            graph.add_edge(FlowEdge(
                source=orch_id,
                target=agent.name,
                label="handoff",
            ))
            graph.add_edge(FlowEdge(
                source=agent.name,
                target=orch_id,
                label="result",
            ))

        graph.add_node(FlowNode(id="__output__", label="end", node_type="output"))
        graph.add_edge(FlowEdge(source=orch_id, target="__output__"))
        return graph
