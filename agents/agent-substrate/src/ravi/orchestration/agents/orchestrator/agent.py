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
from pydantic import BaseModel
from ravi.kernel.messages.content import JsonObject, TextBlock

from ravi.fabric.actors.actor import ActorAgent
from ravi.kernel.plugin import register_agent
from ravi.reasoning.agents.assistant.agent import AssistantAgent
from ravi.fabric.agents_base.agent_result import AgentRunResult
from ravi.fabric.agents_base.agent_context import AgentContext
from ravi.kernel.guardrails.base_guardrail import BaseGuardrail
from ravi.reasoning.hooks.manager import HookEvent, HookManager
from ravi.kernel.memory.history_provider import HistoryProvider
from ravi.kernel.memory.memory_scope import MemoryScope
from ravi.kernel.llm.base_client import BaseModelClient
from ravi.shared.observability import logger
from ravi.fabric.resilience.policies import RetryPolicy
from ravi.kernel.tools.base_tool import BaseTool, ToolResult
from ravi.catalog import SkillManager
from ravi.catalog.tools.human_input.tool import ToolApprovalHandler
from ravi.kernel.runtime import AgentRuntime
from ravi.fabric.catalog import AgentCatalogRegistry
from ravi.kernel.middleware.base import BaseMiddleware


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

    def __init__(
        self,
        agent: ActorAgent,
        runtime: Optional[AgentRuntime] = None,
        orchestrator: Optional[ActorAgent] = None,
    ) -> None:
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
        self._runtime = runtime
        self._orchestrator = orchestrator

    @property
    def target_agent(self) -> ActorAgent:
        return self._agent

    async def execute(self, *, input: str, reason: str = "", **_kwargs) -> ToolResult:  # noqa: A002
        """Run the sub-agent and return its output as a ToolResult.

        When a runtime is available and the sub-agent has an ``agent_id``,
        dispatches via ``runtime.send_message()`` instead of calling
        ``agent.run()`` directly.  This enables distributed execution.
        """
        logger.debug(
            "Handoff → %s | reason: %s | input: %.80s…",
            self._agent.name,
            reason,
            input,
        )

        # Propagate execution context with depth tracking
        exec_ctx = None
        orchestrator_ctx = getattr(self._orchestrator, "execution_context", None)
        if orchestrator_ctx is not None:
            try:
                exec_ctx = orchestrator_ctx.child_context(self._agent.name)
            except Exception:
                pass  # MaxAgentDepthError — will propagate naturally via run()

        # Distributed path: dispatch via runtime
        if self._runtime is not None:
            output_text = await self._runtime.send_message(
                input,
                sender=None,
                recipient=self._agent.id,
            )
            if isinstance(output_text, list):
                output_text = "\n".join(
                    str(p) for p in output_text if isinstance(p, str)
                )
            return ToolResult(
                content=[TextBlock(text=str(output_text) or "(no output)")],
            )

        # Local path: call run() directly (backward compatible)
        if exec_ctx is not None:
            self._agent.execution_context = exec_ctx
        result: AgentRunResult = await self._agent.run(input)
        output_text = result.output
        if isinstance(output_text, list):
            output_text = "\n".join(str(p) for p in output_text if isinstance(p, str))
        return ToolResult(
            content=[TextBlock(text=output_text or "(no output)")],
            is_error=result.status.value in ("error", "guardrail_tripped"),
        )


# ---------------------------------------------------------------------------
# OrchestratorAgent
# ---------------------------------------------------------------------------


class HandoffEventPayload(BaseModel):
    """Structured payload emitted when the orchestrator delegates to a sub-agent."""

    event: str = "on_handoff"
    from_agent: str
    to_agent: str
    input: str
    reason: str


@register_agent("orchestrator")
class OrchestratorAgent(AssistantAgent):
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
        model_context:        AgentContext strategy for the orchestrator.
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
        runtime:              Optional runtime for distributed sub-agent dispatch.
        agent_id:             Identity for this orchestrator in the runtime.
    """

    def __init__(
        self,
        name: str,
        description: str,
        *,
        model_client: BaseModelClient,
        sub_agents: List[ActorAgent],
        system_instructions: Optional[str] = None,
        memory: Optional[HistoryProvider] = None,
        memory_scope: MemoryScope = MemoryScope.ISOLATED,
        model_context: Optional[AgentContext] = None,
        max_iterations: int = 50,
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
        runtime: Optional[AgentRuntime] = None,
        middleware: Optional[List[BaseMiddleware]] = None,
        catalog: Optional[AgentCatalogRegistry] = None,
    ) -> None:
        if not sub_agents:
            raise ValueError("OrchestratorAgent requires at least one sub_agent")

        # Build handoff tools from sub-agents
        handoff_tools: List[_HandoffTool] = [
            _HandoffTool(agent, runtime=runtime, orchestrator=None)
            for agent in sub_agents
        ]
        all_tools = handoff_tools + (extra_tools or [])

        # Build roster description for the system prompt
        roster = "\n".join(f"  - {a.name}: {a.description}" for a in sub_agents)
        default_instructions = (
            "You are an orchestrator agent. Your job is to analyse the user's "
            "request and delegate to the most appropriate specialist agent.\n\n"
            f"Available specialists:\n{roster}\n\n"
            "Always choose the agent best suited to the task. You may call "
            "multiple agents in sequence if needed. Synthesize their outputs "
            "into a coherent final answer."
        )

        resolved_middleware = list(middleware or [])
        if handoff_guardrails:
            from ravi.reasoning.middleware.guardrails import GuardrailsMiddleware

            resolved_middleware = [
                GuardrailsMiddleware(tool_call_guardrails=handoff_guardrails)
            ] + resolved_middleware

        # Build slim tool catalog
        resolved_catalog: AgentCatalogRegistry = catalog or AgentCatalogRegistry()
        for tool in all_tools:
            resolved_catalog.register_tool(tool)
        if skill_manager is not None:
            resolved_catalog.init_skills(skill_manager)
        elif skill_dirs:
            import os

            resolved_dirs = [os.path.expanduser(d) for d in skill_dirs if d]
            resolved_catalog.init_skills(
                SkillManager(skill_dirs=resolved_dirs, auto_discover=True)
            )

        if runtime is None:
            raise ValueError(
                "OrchestratorAgent requires a runtime — "
                "pass runtime=LocalRuntime() or the server's app.state.runtime"
            )
        from ravi.fabric.memory.in_memory import InMemoryHistoryProvider as _IMP
        from ravi.reasoning.memory.context.sliding_window import SlidingWindowStrategy

        resolved_history = memory or _IMP()
        if model_context is None:
            strategies = [SlidingWindowStrategy(max_messages=40)]
        elif isinstance(model_context, list):
            strategies = model_context
        else:
            strategies = [model_context]

        model_context_mgr = AgentContext(history=resolved_history, compaction_strategies=strategies)

        super().__init__(
            name,
            runtime,
            description=description,
            model=model_client,
            context=model_context_mgr,
            catalog=resolved_catalog,
            system_instructions=system_instructions or default_instructions,
            memory_scope=memory_scope,
            max_iterations=max_iterations,
            verbose=verbose,
            hooks=hooks,
            llm_retry_policy=llm_retry_policy,
            tool_retry_policy=tool_retry_policy,
            run_timeout=run_timeout,
            tool_timeout=tool_timeout,
            tool_approval_handler=tool_approval_handler,
            tools_requiring_approval=tools_requiring_approval,
            middleware=resolved_middleware,
            enable_capability_search=False,
        )

        self.sub_agents = sub_agents
        self._handoff_tools: Dict[str, _HandoffTool] = {
            t.name: t
            for t in handoff_tools  # type: ignore[misc]
        }


        # Back-patch orchestrator reference now that self is available
        for ht in handoff_tools:
            ht._orchestrator = self

        # Patch the hook dispatcher so every handoff emits HANDOFF event
        self._patch_hooks()

    # -- Hook patching --------------------------------------------------------

    def _patch_hooks(self) -> None:
        """Wrap the underlying hooks dispatcher to intercept handoff tool calls."""
        original_dispatch = self.hooks.dispatch

        async def _patched_dispatch(event: HookEvent, payload: JsonObject) -> None:
            if event == HookEvent.TOOL_START:
                tool_name = payload.get("tool_name", "")
                if isinstance(tool_name, str) and tool_name.startswith("handoff_"):
                    agent_name = tool_name[len("handoff_") :]
                    args = payload.get("tool_arguments", {})
                    if not isinstance(args, dict):
                        args = {}

                    handoff_payload = HandoffEventPayload(
                        from_agent=self.name,
                        to_agent=agent_name,
                        input=str(args.get("input", "")),
                        reason=str(args.get("reason", "")),
                    )
                    await original_dispatch(
                        HookEvent.HANDOFF,
                        handoff_payload.model_dump(mode="json"),
                    )
            await original_dispatch(event, payload)

        self.hooks.dispatch = _patched_dispatch  # type: ignore[method-assign]


