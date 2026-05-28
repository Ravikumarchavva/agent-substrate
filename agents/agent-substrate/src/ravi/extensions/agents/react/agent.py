"""ReAct (Reasoning + Acting) agent implementation.

The agent operates in a loop:
  1. THINK  — call the LLM with current memory
  2. ACT    — execute any requested tool calls
  3. OBSERVE — store results back into memory
  4. Repeat until the LLM stops requesting tools or max_iterations is hit

Key design decisions:
  - Tool-call parsing is centralised in _parse_tool_call() — one place to handle
    every shape the SDK might emit.
  - Tool execution is centralised in _execute_tool() — handles lookup, error
    wrapping, and timing.
  - Every LLM call produces exactly one StepResult.
  - The final AgentRunResult contains zero duplication.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, AsyncIterator, Dict, List, Optional, Tuple, Union

if TYPE_CHECKING:
    from ravi.kernel.safeguards._mutation import MutationPolicy
from uuid import uuid4

from ravi.kernel.messages.content import JsonObject
from ravi.kernel.messages._types import StreamChunk


from ravi.kernel.agents.base_agent import BaseAgent
from ravi.kernel.plugin import register_agent
from ravi.extensions.agents.react._react_loop import (
    build_persisted_user_message,
    extract_text,
    normalize_textual_tool_calls,
    prepare_model_context_messages,
    resolve_user_message_content,
)
from ravi.kernel.execution.context import ExecutionContext
from ravi.kernel.runtime import AgentId, AgentRuntime, StreamPublisher, TopicId
from ravi.kernel.agents.agent_result import (
    AgentRunResult,
    AggregatedUsage,
    RunStatus,
    StepResult,
    ToolCallRecord,
)
from ravi.extensions.agents.react._guardrail_runner import (
    build_guardrail_tripped_result,
    build_tool_blocked_message,
    build_tool_blocked_record,
    check_input_guardrails,
    check_tool_call_guardrails,
)
from ravi.extensions.agents.react._tool_execution import (
    ParsedToolCall,
    ToolExecutionContext,
    build_tool_error,
    execute_tool_direct,
    execute_tool_via_runtime,
    find_tool,
    parse_tool_call,
    request_tool_approval,
    tool_needs_approval,
)
from ravi.extensions.agents.react._stream_handler import (
    handle_stream_final_response,
    process_stream_tool_calls,
)
from ravi.exceptions import GuardrailTripwireError
from ravi.kernel.guardrails.base_guardrail import (
    GuardrailResult,
    GuardrailType,
)
from ravi.kernel.hooks import HookEvent, HookManager
from ravi.kernel.memory.base_memory import BaseMemory
from ravi.kernel.memory.memory_scope import MemoryScope
from ravi.kernel.memory.unbounded_memory import UnboundedMemory
from ravi.kernel.messages.base_message import BaseClientMessage
from ravi.kernel.middleware.base import (
    BaseMiddleware,
    MiddlewareContext,
    MiddlewareStage,
)
from ravi.kernel.structured import StructuredOutputResult
from ravi.kernel.messages.client_messages import (
    AssistantMessage,
    SystemMessage,
    ToolExecutionResultMessage,
)
from ravi.kernel.messages._types import MediaType
from ravi.kernel.llm.base_client import GenerateResult
from ravi.shared.observability import global_metrics, global_tracer, logger
from ravi.catalog.tools.human_input.tool import (
    ToolApprovalHandler,
)
from ravi.extensions.resilience.policies import (
    LLM_RETRY_POLICY,
    RetryPolicy,
    TOOL_RETRY_POLICY,
    _calculate_delay,
)
from ravi.kernel.agent_catalog import AgentCatalogRegistry
from ravi.catalog import SkillManager
from ravi.catalog.tools.capability_search.tool import CapabilitySearchTool
from ravi.kernel.runtime import CheckpointStore


# ---------------------------------------------------------------------------
# Backward-compat alias — ParsedToolCall now lives in _tool_execution.py
# ---------------------------------------------------------------------------

_ParsedToolCall = ParsedToolCall


# ---------------------------------------------------------------------------
# ReActAgent
# ---------------------------------------------------------------------------


@register_agent("react")
class ReActAgent(BaseAgent):
    """Reasoning + Acting agent with tool calling loop.

    All resources (model, memory, context, tools, skills, checkpointing) are
    registered in the catalog before construction. Config-only params
    (numbers, booleans, policies) are passed directly.

    Usage::

        catalog = AgentCatalogRegistry()
        catalog.register_model("primary", OpenAIClient(model="gpt-4o"))
        catalog.register_tool(search_tool)
        catalog.register_tool(calc_tool)
        # optional — defaults are injected automatically:
        # catalog.register_memory("main", RedisMemory(...))
        # catalog.register_context("main", SlidingWindowContext(max_messages=40))
        # catalog.init_skills(skill_dirs=["./skills"])

        agent = ReActAgent(
            name="researcher",
            description="Answers questions using web tools",
            catalog=catalog,
        )
        result = await agent.run("Find the top 3 repos for user X on GitHub")
        print(result.output)
        print(result.summary())
    """

    DEFAULT_MAX_ACTIVE_TOOLS = 8

    def __init__(
        self,
        name: str,
        description: str,
        *,
        catalog: AgentCatalogRegistry,
        system_instructions: str = (
            "You are a helpful AI assistant. Use the provided tools to solve "
            "the user's request. Think step-by-step."
        ),
        memory_scope: MemoryScope = MemoryScope.ISOLATED,
        max_iterations: int = 50,
        verbose: bool = True,
        # Production features
        hooks: Optional[HookManager] = None,
        llm_retry_policy: Optional[RetryPolicy] = None,
        tool_retry_policy: Optional[RetryPolicy] = None,
        run_timeout: Optional[float] = None,
        tool_timeout: Optional[float] = 30.0,
        # HITL: Tool approval
        tool_approval_handler: Optional[ToolApprovalHandler] = None,
        tools_requiring_approval: Optional[List[str]] = None,
        # Structured output
        response_schema: Optional[type] = None,
        # Middleware
        middleware: Optional[List[BaseMiddleware]] = None,
        execution_context: Optional[ExecutionContext] = None,
        # Runtime
        runtime: Optional[AgentRuntime] = None,
        agent_id: Optional[AgentId] = None,
        enable_capability_search: bool = True,
        # Fault recovery — checkpointing
        checkpoint_every: int = 0,  # 0 = disabled; N > 0 = checkpoint every N steps
        # Self-evolution safeguard — gates dynamic tool/prompt mutations
        mutation_policy: Optional[MutationPolicy] = None,
    ):
        # Inject defaults for resources not explicitly registered in the catalog.
        if catalog.primary_memory() is None:
            catalog.register_memory("memory", UnboundedMemory())

        if catalog.primary_context() is None:
            from ravi.extensions.context.redis_model_context import SlidingWindowContext

            catalog.register_context("default", SlidingWindowContext(max_messages=40))

        # Inject the capability search tool
        if enable_capability_search and catalog.get_tool("capability_search") is None:
            catalog.register_tool(CapabilitySearchTool(catalog))

        super().__init__(
            name=name,
            description=description,
            catalog=catalog,
            system_instructions=system_instructions,
            memory_scope=memory_scope,
            prompt_enricher=catalog,
            response_schema=response_schema,
            middleware=middleware,
            execution_context=execution_context,
            runtime=runtime,
            agent_id=agent_id,
        )
        self._catalog = self.catalog
        # Narrow type: memory is always non-None after the default injection above.
        self.memory: BaseMemory = self.memory  # type: ignore[assignment]
        self.skill_manager: Optional[SkillManager] = catalog.skill_manager
        self.max_iterations = max_iterations
        self.verbose = verbose

        # Production features
        self.hooks = hooks or HookManager()
        self.llm_retry_policy = llm_retry_policy or LLM_RETRY_POLICY
        self.tool_retry_policy = tool_retry_policy or TOOL_RETRY_POLICY
        self.run_timeout = run_timeout
        self.tool_timeout = tool_timeout

        # HITL: tool approval
        self.tool_approval_handler = tool_approval_handler
        self.tools_requiring_approval = tools_requiring_approval
        self._active_tool_names: set[str] = set()
        self._tool_search_name = "capability_search"
        self._always_visible_tool_names = {
            name
            for name in (self._tool_search_name, "ask_human")
            if self._catalog.get_tool(name) is not None
        }
        self._max_active_tools = self.DEFAULT_MAX_ACTIVE_TOOLS
        # Fault recovery
        self.checkpoint_store: Optional[CheckpointStore] = (
            catalog.primary_checkpoint_store()
        )
        self.checkpoint_every: int = checkpoint_every
        # Self-evolution safeguard
        self._mutation_policy: Optional[MutationPolicy] = mutation_policy

    # ── Core run ─────────────────────────────────────────────────────────────

    def get_system_instructions(self) -> str:
        """Return the current system instructions (implements BaseAgent abstract method)."""
        return self._system_instructions

    # ── Self-evolution mutation gates ─────────────────────────────────────────

    async def add_tool(self, tool: object) -> bool:
        """Dynamically register a tool — gated by :attr:`mutation_policy`.

        When no ``mutation_policy`` was configured, the tool is registered
        unconditionally and ``True`` is returned.  When a policy is
        present, ``MutationKind.TOOL_ADD`` is evaluated; the tool is only
        registered when the policy grants permission.

        Returns:
            ``True`` if the tool was registered; ``False`` if the policy denied.
        """
        if self._mutation_policy is not None:
            from datetime import datetime, timezone
            from uuid import uuid4
            from ravi.kernel.safeguards._mutation import MutationKind, MutationRequest

            request = MutationRequest(
                request_id=uuid4().hex,
                principal_fqn=self.name,
                target_agent_fqn=self.name,
                kind=MutationKind.TOOL_ADD,
                family_depth=0,
                payload_summary=getattr(tool, "name", repr(tool))[:100],
                requested_at=datetime.now(timezone.utc).isoformat(),
            )
            permission = await self._mutation_policy.evaluate(request)
            if not permission.granted:
                return False

        self._catalog.register_tool(tool)  # type: ignore[arg-type]
        return True

    async def rewrite_system_prompt(self, new_instructions: str) -> bool:
        """Rewrite the agent's system instructions — gated by :attr:`mutation_policy`.

        When no ``mutation_policy`` was configured, the rewrite is applied
        unconditionally and ``True`` is returned.  When a policy is present,
        ``MutationKind.PROMPT_REWRITE`` is evaluated first.

        Returns:
            ``True`` if the rewrite was applied; ``False`` if the policy denied.
        """
        if self._mutation_policy is not None:
            from datetime import datetime, timezone
            from uuid import uuid4
            from ravi.kernel.safeguards._mutation import MutationKind, MutationRequest

            request = MutationRequest(
                request_id=uuid4().hex,
                principal_fqn=self.name,
                target_agent_fqn=self.name,
                kind=MutationKind.PROMPT_REWRITE,
                family_depth=0,
                payload_summary=new_instructions[:100],
                requested_at=datetime.now(timezone.utc).isoformat(),
            )
            permission = await self._mutation_policy.evaluate(request)
            if not permission.granted:
                return False

        self._update_system_instructions(new_instructions)
        return True

    async def reset(self) -> None:
        """Clear memory and return agent to initial state."""
        await super().reset()
        self._reset_tool_activation_state()
        # System instructions are not stored in memory — they are passed
        # as an explicit kwarg on every LLM call from _system_instructions.
        self._reset_hitl_tools()

    def _reset_hitl_tools(self) -> None:
        """Reset per-run state on any tool exposing ``reset()`` (structural match)."""
        from ravi.kernel.tools.base_tool import ResettableTool

        for tool in self.tools:
            if isinstance(tool, ResettableTool):
                tool.reset()

    def _reset_tool_activation_state(self) -> None:
        """Clear the currently advertised tool subset between runs."""
        self._active_tool_names.clear()

    # ── Checkpoint helpers ────────────────────────────────────────────────────

    async def _save_checkpoint(
        self,
        run_id: str,
        iteration: int,
        steps: list[StepResult],
    ) -> None:
        """Persist current agent state to the checkpoint store using RunCheckpoint."""
        if self.checkpoint_store is None:
            return
        from ravi.kernel.runtime import RunCheckpoint

        agent_id = (
            self.execution_context.agent_id
            if self.execution_context and self.execution_context.agent_id
            else self.name
        )
        thread_id = self.execution_context.thread_id if self.execution_context else ""
        messages = await self.memory.get_messages()

        # Load existing tree or root
        root = await self.checkpoint_store.load(run_id, agent_id)
        if root is None:
            root = RunCheckpoint(
                run_id=run_id,
                agent_id=agent_id,
                thread_id=thread_id,
            )

        root.mark_in_progress(iteration=iteration)
        root.messages = [m.model_dump(mode="json") for m in messages]

        # Capture resource locks from runtime if available
        if self.runtime and hasattr(self.runtime, "resource_locks"):
            root.resource_locks = self.runtime.resource_locks.snapshot()

        await self.checkpoint_store.save(root)
        logger.debug(
            "[%s] RunCheckpoint saved: iteration=%d run_id=%s status=%s",
            self.name,
            iteration,
            run_id,
            root.status,
        )

    async def run_with_recovery(
        self,
        input_text: str,
        *,
        response_schema: Optional[type] = None,
        **kwargs,
    ) -> AgentRunResult:
        """Run with automatic tree-structured checkpoint recovery.

        If a checkpoint exists for the current (run_id, agent_id) pair, the
        agent's memory is restored from the checkpoint before running.  This
        allows long runs to survive process restarts or transient failures.

        Usage::

            store = InMemoryCheckpointStore()
            agent = ReActAgent(..., checkpoint_store=store, checkpoint_every=5)
            # If the process crashed mid-run, this will resume from step 5:
            result = await agent.run_with_recovery("Do the long task")
        """
        if self.checkpoint_store is not None:
            run_id = self._resolve_run_id()
            agent_id = (
                self.execution_context.agent_id
                if self.execution_context and self.execution_context.agent_id
                else self.name
            )
            checkpoint = await self.checkpoint_store.load(run_id, agent_id)
            if checkpoint is not None and checkpoint.messages:
                # Restore memory from tree checkpoint
                from ravi.kernel.messages.client_messages import (
                    AssistantMessage,
                    SystemMessage,
                    ToolCallMessage,
                    ToolExecutionResultMessage,
                    UserMessage,
                )

                await self.memory.clear()
                message_classes: dict[str, type[BaseClientMessage]] = {
                    "system": SystemMessage,
                    "user": UserMessage,
                    "assistant": AssistantMessage,
                    "tool_call": ToolCallMessage,
                    "tool": ToolExecutionResultMessage,
                }
                for msg_dict in checkpoint.messages:
                    role = str(msg_dict.get("role", ""))
                    cls = message_classes.get(role)
                    if cls is not None:
                        try:
                            await self.memory.add_message(cls.model_validate(msg_dict))
                        except Exception:
                            pass  # Skip malformed messages
                logger.info(
                    "[%s] Restored from tree checkpoint: iteration=%d status=%s",
                    self.name,
                    checkpoint.iteration,
                    checkpoint.status,
                )

                # Restore any saved resource locks into runtime
                if (
                    self.runtime
                    and checkpoint.resource_locks
                    and hasattr(self.runtime, "resource_locks")
                ):
                    for lock_data in checkpoint.resource_locks:
                        try:
                            await self.runtime.resource_locks.acquire(
                                resource_uri=lock_data["resource_uri"],
                                agent_id=lock_data["holder_agent_id"],
                                mode=lock_data["mode"],
                            )
                        except Exception as e:
                            logger.warning("Failed to restore lock on recovery: %s", e)
        return await self.run(input_text, response_schema=response_schema, **kwargs)

    def _resolve_run_id(self) -> str:
        """Return the active execution run id or create a new one."""
        if self.execution_context is not None and self.execution_context.run_id:
            return self.execution_context.run_id
        return str(uuid4())

    async def _record_lineage(
        self,
        msg: BaseClientMessage,
        parent_message_id: Optional[str] = None,
        tool_call_id: Optional[str] = None,
    ) -> None:
        """Tag message write with lineage causal provenance DAG metadata."""
        try:
            session_mgr = self._catalog.resolve("session_manager")
        except Exception:
            session_mgr = None
            
        if session_mgr is None or not hasattr(session_mgr, "record_lineage"):
            return
            
        from ravi.kernel.memory._lineage import ProvenanceTag
        prov = ProvenanceTag(
            agent_fqn=self.name,
            activation_id=getattr(self, "_current_run_id", "unknown"),
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            tool_call_id=tool_call_id,
            parent_message_id=parent_message_id,
            trust_score=None,
        )
        session_id = getattr(self, "_session_id", self.name)
        await session_mgr.record_lineage(session_id, msg.id, prov)

    @staticmethod
    def _resolve_requested_tool_choice(
        tool_schemas: List[JsonObject],
        requested_tool_choice: Optional[str | JsonObject] = None,
    ) -> Optional[str | JsonObject]:
        """Return the tool-choice mode for the current LLM step.

        Most turns should stay on automatic tool selection. For a small set of
        high-confidence routed intents, the caller can force the first step to
        open a specific tool by name and then let the normal ReAct loop resume.
        """
        if not tool_schemas:
            return None
        if requested_tool_choice:
            return requested_tool_choice
        return "auto"

    def _current_middleware_run_id(self) -> str:
        current = getattr(self, "_current_run_id", "")
        if current:
            return current
        if self.execution_context is not None and self.execution_context.run_id:
            return self.execution_context.run_id
        return ""

    async def run(
        self,
        input_text: str,
        *,
        response_schema: Optional[type] = None,
        **kwargs,
    ) -> AgentRunResult:
        self._reset_tool_activation_state()
        _schema = (
            response_schema if response_schema is not None else self.response_schema
        )
        # Apply run-level timeout if configured
        if self.run_timeout:
            return await asyncio.wait_for(
                self._run_inner(input_text, response_schema=_schema, **kwargs),
                timeout=self.run_timeout,
            )
        return await self._run_inner(input_text, response_schema=_schema, **kwargs)

    async def _run_inner(
        self,
        input_text: str,
        *,
        response_schema: Optional[type] = None,
        **kwargs,
    ) -> AgentRunResult:
        run_id = self._resolve_run_id()
        self._current_run_id = run_id
        run_start = datetime.now(timezone.utc)
        usage = AggregatedUsage()
        steps: List[StepResult] = []
        tool_calls_by_name: Dict[str, int] = {}
        total_tool_calls = 0
        status = RunStatus.COMPLETED
        error_msg: Optional[str] = None
        final_output: List[MediaType] = []  # Multimodal output
        guardrail_results: List[GuardrailResult] = []
        response: Optional[AssistantMessage] = None
        _run_end_dispatched = False  # guarantees RUN_END fires exactly once
        initial_tool_choice = kwargs.pop("tool_choice", None)

        # Extract guardrails from GuardrailsMiddleware if present
        input_guardrails: List = []
        output_guardrails: List = []
        if self.middleware_pipeline and self.middleware_pipeline.middleware:
            for mw in self.middleware_pipeline.middleware:
                if mw.name == "guardrails":
                    input_guardrails = getattr(mw, "input_guardrails", [])
                    output_guardrails = getattr(mw, "output_guardrails", [])
                    break

        attrs = {"agent_name": self.name, "input_length": len(input_text)}
        user_message_content = resolve_user_message_content(
            input_text,
            kwargs.pop("input_content", None),
        )
        persisted_user_message = build_persisted_user_message(
            input_text,
            user_message_content,
        )

        try:
            with global_tracer.start_span("agent_run", attrs) as run_span:
                global_metrics.increment_counter("agent_runs", tags={"name": self.name})
                if self.verbose:
                    logger.info(f"[{self.name}] Starting run: {input_text[:80]}...")

                # ── LIFECYCLE HOOK: RUN_START ─────────────────────────────
                await self.hooks.dispatch(
                    HookEvent.RUN_START,
                    {
                        "event": "on_run_start",
                        "agent_name": self.name,
                        "run_id": run_id,
                        "input_text": input_text,
                    },
                )

                # 1. Add user message (system instructions travel via kwarg, not memory)
                await self.memory.add_message(persisted_user_message)
                await self._record_lineage(persisted_user_message)
                last_msg_id = persisted_user_message.id

                # 2. ReAct loop
                for step_num in range(1, self.max_iterations + 1):
                    with global_tracer.start_span(
                        f"step_{step_num}", {"step": step_num}
                    ):
                        # Honour cancellation / deadline from ExecutionContext
                        if (
                            self.execution_context is not None
                            and not self.execution_context.is_alive
                        ):
                            status = RunStatus.CANCELLED
                            error_msg = (
                                "Execution context cancelled or deadline exceeded"
                            )
                            break

                        # A. THINK — call LLM
                        try:
                            response = await self._call_llm(
                                current_input=input_text,
                                response_schema=response_schema,
                                input_content=user_message_content,
                                tool_choice=(
                                    initial_tool_choice if step_num == 1 else None
                                ),
                                **kwargs,
                            )
                        except GuardrailTripwireError as e:
                            logger.error(
                                f"[{self.name}] Guardrail tripwire in middleware: {e.message}"
                            )
                            tripped_res = e.details.get("result", {})
                            g_type = tripped_res.get("guardrail_type")
                            output_prefix = (
                                "Request blocked"
                                if g_type == GuardrailType.INPUT
                                else "Response blocked"
                            )
                            return build_guardrail_tripped_result(
                                error=e,
                                run_id=run_id,
                                agent_name=self.name,
                                run_start=run_start,
                                steps=steps,
                                usage=usage,
                                max_iterations=self.max_iterations,
                                guardrail_results=guardrail_results,
                                output_prefix=output_prefix,
                            )

                        usage.add(response.usage)
                        await self.memory.add_message(response)
                        await self._record_lineage(response, parent_message_id=last_msg_id)
                        last_msg_id = response.id

                        thought_content = response.content if response.content else None

                        if not response.tool_calls:
                            if self.verbose:
                                logger.info(
                                    f"[{self.name}] Step {step_num}: final answer"
                                )
                            run_span.set_attribute("final_step", step_num)

                            steps.append(
                                StepResult(
                                    step=step_num,
                                    thought=thought_content,
                                    tool_calls=[],
                                    usage=response.usage,
                                    finish_reason=response.finish_reason or "stop",
                                )
                            )
                            final_output = thought_content or []
                            break

                        if self.verbose:
                            names = [
                                parse_tool_call(tc).name for tc in response.tool_calls
                            ]
                            logger.info(
                                f"[{self.name}] Step {step_num}: tool calls → {names}"
                            )

                        tool_records: List[ToolCallRecord] = []
                        for tc_raw in response.tool_calls:
                            parsed = parse_tool_call(tc_raw)

                            try:
                                await check_tool_call_guardrails(
                                    input_guardrails=input_guardrails,
                                    output_guardrails=output_guardrails,
                                    agent_name=self.name,
                                    run_id=run_id,
                                    parsed=parsed,
                                )
                            except GuardrailTripwireError as e:
                                tool_msg = build_tool_blocked_message(parsed, e.message)
                                record = build_tool_blocked_record(parsed, e.message)
                                await self.memory.add_message(tool_msg)
                                await self._record_lineage(
                                    tool_msg, 
                                    parent_message_id=last_msg_id, 
                                    tool_call_id=parsed.id
                                )
                                tool_records.append(record)
                                tool_calls_by_name[parsed.name] = (
                                    tool_calls_by_name.get(parsed.name, 0) + 1
                                )
                                total_tool_calls += 1
                                continue

                            record, tool_msg = await self._execute_tool(
                                parsed, step_num
                            )
                            await self.memory.add_message(tool_msg)
                            await self._record_lineage(
                                tool_msg, 
                                parent_message_id=last_msg_id, 
                                tool_call_id=parsed.id
                            )
                            tool_records.append(record)

                            tool_calls_by_name[parsed.name] = (
                                tool_calls_by_name.get(parsed.name, 0) + 1
                            )
                            total_tool_calls += 1

                        steps.append(
                            StepResult(
                                step=step_num,
                                thought=thought_content,
                                tool_calls=tool_records,
                                usage=response.usage,
                                finish_reason="tool_calls",
                            )
                        )

                        # Save checkpoint if configured
                        if (
                            self.checkpoint_store is not None
                            and self.checkpoint_every > 0
                            and step_num % self.checkpoint_every == 0
                        ):
                            await self._save_checkpoint(run_id, step_num, steps)

                else:
                    # Loop exhausted without breaking → max iterations
                    status = RunStatus.MAX_ITERATIONS
                    if self.verbose:
                        logger.warning(
                            f"[{self.name}] Hit max iterations ({self.max_iterations})"
                        )
                    # Try to extract whatever the last response said
                    if steps and steps[-1].thought:
                        final_output = steps[-1].thought

                # 3. Build result
                run_end = datetime.now(timezone.utc)
                duration = (run_end - run_start).total_seconds()

                result = AgentRunResult(
                    run_id=run_id,
                    agent_name=self.name,
                    output=final_output,
                    status=status,
                    steps=steps,
                    usage=usage,
                    tool_calls_total=total_tool_calls,
                    tool_calls_by_name=tool_calls_by_name,
                    start_time=run_start,
                    end_time=run_end,
                    duration_seconds=duration,
                    max_iterations=self.max_iterations,
                    error=error_msg,
                    guardrail_results=guardrail_results,
                )

                # 4. Structured extraction (native — no extra LLM call)
                if response_schema is not None and status == RunStatus.COMPLETED:
                    # The model was given `text.format` + `tools` in each LLM
                    # call; the final answer's text is already schema-conformant.
                    # Check AssistantMessage.parsed first (set by generate()).
                    _last_msg = steps[-1] if steps else None
                    _parsed = None
                    if _last_msg is not None and response is not None:
                        # response is still the last LLM AssistantMessage from
                        # the loop; its `.parsed` was populated by generate().
                        _parsed = getattr(response, "parsed", None)

                    if _parsed is not None:
                        from ravi.kernel.structured.result import StructuredOutputResult

                        assert response is not None
                        raw_text = extract_text(response) or ""
                        result.structured_output = StructuredOutputResult(
                            parsed=_parsed,
                            raw_text=raw_text,
                            model=getattr(self.model_client, "model", None),
                        )
                    else:
                        # Fallback: extra LLM call (when the model didn't
                        # produce valid structured text in its final answer).
                        memory_messages = await self.memory.get_messages()
                        context_messages = await self.model_context.build(
                            session_id=getattr(self, "_session_id", self.name),
                            current_input=input_text,
                            raw_messages=prepare_model_context_messages(
                                memory_messages,
                                input_text,
                                user_message_content,
                            ),
                            model_client=self.model_client,
                        )
                        clean_ctx = [
                            m for m in context_messages if not isinstance(m, SystemMessage)
                        ]
                        result.structured_output = await self.model_client.generate(  # type: ignore[assignment]
                            clean_ctx,
                            system_instructions=self.get_effective_system_prompt(),
                            response_format=response_schema,
                        )

                # ── LIFECYCLE HOOK: RUN_END ──────────────────────────────
                _run_end_dispatched = True
                await self.hooks.dispatch(
                    HookEvent.RUN_END,
                    {
                        "event": "on_run_end",
                        "agent_name": self.name,
                        "run_id": run_id,
                        "status": status.value,
                        "steps_used": len(steps),
                        "tool_calls_total": total_tool_calls,
                        "tokens_used": usage.total_tokens,
                        "duration_seconds": duration,
                    },
                )

                if self.checkpoint_store is not None:
                    agent_id = (
                        self.execution_context.agent_id
                        if self.execution_context and self.execution_context.agent_id
                        else self.name
                    )
                    root = await self.checkpoint_store.load(run_id, agent_id)
                    if root is not None:
                        root.mark_completed(result=result.model_dump(mode="json"))
                        await self.checkpoint_store.save(root)

                return result
        except Exception as exc:
            if not _run_end_dispatched:
                try:
                    run_end_err = datetime.now(timezone.utc)
                    await self.hooks.dispatch(
                        HookEvent.RUN_END,
                        {
                            "event": "on_run_end",
                            "agent_name": self.name,
                            "run_id": run_id,
                            "status": "error",
                            "steps_used": len(steps),
                            "tool_calls_total": total_tool_calls,
                            "tokens_used": usage.total_tokens,
                            "duration_seconds": (
                                run_end_err - run_start
                            ).total_seconds(),
                            "error": str(exc),
                        },
                    )
                except Exception:
                    pass  # never let hook dispatch prevent checkpoint saving
            if self.checkpoint_store is not None:
                agent_id = (
                    self.execution_context.agent_id
                    if self.execution_context and self.execution_context.agent_id
                    else self.name
                )
                root = await self.checkpoint_store.load(run_id, agent_id)
                if root is not None:
                    root.mark_failed(error=str(exc))
                    await self.checkpoint_store.save(root)
            raise exc
        finally:
            self._current_run_id = ""

    # ── Structured-output run ────────────────────────────────────────────────

    async def run_structured(
        self,
        input_text: str,
        schema: "type",
        *,
        max_iterations: Optional[int] = None,
        **kwargs,
    ) -> "StructuredOutputResult":
        """Run the full ReAct loop and then emit a typed final answer.

        Delegates to ``run(response_schema=schema)`` — the structured
        extraction is now built into the core loop.

        Args:
            input_text: User input to process.
            schema: Pydantic ``BaseModel`` subclass for the final answer.
            max_iterations: Override the agent's default ``max_iterations``.
            **kwargs: Passed through to ``run()``.

        Returns:
            ``StructuredOutputResult[schema]`` with the typed final answer.

        Raises:
            ``StructuredOutputError`` if the model cannot produce a valid
            structured output after the ReAct loop.
        """
        _saved_max = self.max_iterations
        if max_iterations is not None:
            self.max_iterations = max_iterations
        try:
            result = await self.run(input_text, response_schema=schema, **kwargs)
        finally:
            self.max_iterations = _saved_max

        if result.structured_output is None:
            from ravi.kernel.structured import StructuredOutputError

            raise StructuredOutputError(
                "Structured extraction did not produce a result "
                f"(status={result.status.value})"
            )
        return result.structured_output

    # ── Streaming run ────────────────────────────────────────────────────────

    async def run_stream(
        self,
        input_text: str,
        *,
        response_schema: Optional[type] = None,
        **kwargs,
    ) -> AsyncIterator[Union[StreamChunk, dict, ToolExecutionResultMessage]]:
        """Streaming variant — yields partial chunks and tool results.

        Guardrails are applied at the same points as run():
          - Input guardrails: before first LLM call
          - Output guardrails: after final response (on CompletionChunk)
          - Tool-call guardrails: before each tool.execute()

        If an input guardrail trips, yields a single error message and returns.
        """
        self._reset_tool_activation_state()

        # Extract guardrails from GuardrailsMiddleware if present in middleware pipeline
        input_guardrails = []
        output_guardrails = []
        if self.middleware_pipeline and self.middleware_pipeline.middleware:
            for mw in self.middleware_pipeline.middleware:
                if mw.name == "guardrails":
                    input_guardrails = getattr(mw, "input_guardrails", [])
                    output_guardrails = getattr(mw, "output_guardrails", [])
                    break

        run_id = self._resolve_run_id()
        self._current_run_id = run_id
        attrs = {"agent_name": self.name, "input_length": len(input_text)}
        user_message_content = resolve_user_message_content(
            input_text,
            kwargs.pop("input_content", None),
        )
        persisted_user_message = build_persisted_user_message(
            input_text,
            user_message_content,
        )
        initial_tool_choice = kwargs.pop("tool_choice", None)

        # Optional: publish chunks to a topic for remote subscribers
        _stream_pub = None
        if self.runtime is not None and self.agent_id is not None:
            _stream_pub = StreamPublisher(
                self.runtime,
                TopicId(type="stream", source=self.agent_id.key),
                sender=self.agent_id,
            )

        try:
            with global_tracer.start_span("agent_run_stream", attrs):
                global_metrics.increment_counter("agent_runs", tags={"name": self.name})
                if self.verbose:
                    logger.info(
                        f"[{self.name}] Starting streaming run: {input_text[:80]}..."
                    )

                # system instructions travel via kwarg, not memory
                await self.memory.add_message(persisted_user_message)

                # ── INPUT GUARDRAILS ─────────────────────────────────────────
                try:
                    if input_guardrails:
                        await check_input_guardrails(
                            guardrails=input_guardrails,
                            agent_name=self.name,
                            run_id=run_id,
                            input_text=input_text,
                        )
                except GuardrailTripwireError as e:
                    logger.error(f"[{self.name}] Input guardrail tripwire: {e.message}")
                    from ravi.kernel.messages._types import CompletionChunk

                    yield CompletionChunk(
                        message=AssistantMessage(
                            role="assistant",
                            content=[f"Request blocked: {e.message}"],
                            finish_reason="guardrail_tripped",
                        ),
                        metadata={
                            "guardrail_tripped": True,
                            "guardrail": e.guardrail_name,
                        },
                    )
                    if _stream_pub is not None:
                        await _stream_pub.close("guardrail_tripped")
                    return

                for step_num in range(1, self.max_iterations + 1):
                    with global_tracer.start_span(
                        f"step_{step_num}", {"step": step_num}
                    ):
                        # THINK
                        tool_schemas = self._build_tool_schemas(
                            current_input=input_text
                        )
                        memory_messages = await self.memory.get_messages()
                        messages = await self.model_context.build(
                            session_id=getattr(self, "_session_id", self.name),
                            current_input=input_text,
                            raw_messages=prepare_model_context_messages(
                                memory_messages,
                                input_text,
                                user_message_content,
                            ),
                            model_client=self.model_client,
                        )

                        with global_tracer.start_span(
                            "llm_generate_stream", {"msg_count": len(messages)}
                        ):
                            from ravi.kernel.messages._types import (
                                CompletionChunk,
                                TextDeltaChunk,
                            )

                            llm_t0 = asyncio.get_event_loop().time()
                            final_response_obj = None
                            # Accumulate partial text so we can persist it if cancelled
                            # mid-stream before a CompletionChunk is received.
                            partial_text: str = ""

                            stream_messages = [
                                m for m in messages if not isinstance(m, SystemMessage)
                            ]
                            try:
                                async for chunk in self.model_client.generate_stream(
                                    messages=stream_messages,
                                    system_instructions=self.get_effective_system_prompt(),
                                    tools=tool_schemas or None,
                                    tool_choice=self._resolve_requested_tool_choice(
                                        tool_schemas,
                                        initial_tool_choice if step_num == 1 else None,
                                    ),
                                    response_format=(
                                        response_schema
                                        if response_schema is not None
                                        else self.response_schema
                                    ),
                                    **kwargs,
                                ):
                                    yield chunk
                                    if _stream_pub is not None:
                                        await _stream_pub.emit(chunk)

                                    if isinstance(chunk, TextDeltaChunk):
                                        partial_text += chunk.text
                                    elif isinstance(chunk, CompletionChunk):
                                        final_response_obj = chunk.message

                                if final_response_obj:
                                    final_response_obj = (
                                        normalize_textual_tool_calls(
                                            final_response_obj
                                        )
                                    )
                                    await self.memory.add_message(final_response_obj)

                                llm_t1 = asyncio.get_event_loop().time()
                                global_metrics.record_histogram(
                                    "llm_latency",
                                    llm_t1 - llm_t0,
                                    tags={
                                        "model": getattr(
                                            self.model_client, "model", "unknown"
                                        )
                                    },
                                )
                            except asyncio.CancelledError:
                                if final_response_obj is not None:
                                    await self.memory.add_message(final_response_obj)
                                elif partial_text:
                                    await self.memory.add_message(
                                        AssistantMessage(
                                            role="assistant",
                                            content=[partial_text],
                                            finish_reason="cancelled",
                                        )
                                    )
                                raise
                            except Exception as e:
                                global_metrics.increment_counter(
                                    "llm_errors", tags={"error": type(e).__name__}
                                )
                                raise

                        response = final_response_obj or AssistantMessage(
                            role="assistant",
                            content=None,
                        )

                        if not response.tool_calls:
                            if self.verbose:
                                logger.info(
                                    f"[{self.name}] [stream] Step {step_num}: done"
                                )

                            _schema = (
                                response_schema
                                if response_schema is not None
                                else self.response_schema
                            )
                            async for chunk in handle_stream_final_response(
                                response=response,
                                output_guardrails=output_guardrails,
                                agent_name=self.name,
                                run_id=run_id,
                                model_client=self.model_client,
                                model_context=self.model_context,
                                memory=self.memory,
                                input_text=input_text,
                                response_schema=_schema,
                                stream_pub=_stream_pub,
                                extract_text_fn=extract_text,
                            ):
                                yield chunk
                                # If a guardrail tripped, we need to return
                                if (
                                    hasattr(chunk, "metadata")
                                    and isinstance(chunk.metadata, dict)
                                    and chunk.metadata.get("guardrail_tripped")
                                ):
                                    return

                            break

                        if self.verbose:
                            names = [
                                parse_tool_call(tc).name for tc in response.tool_calls
                            ]
                            logger.info(
                                f"[{self.name}] [stream] Step {step_num}: tools → {names}"
                            )

                        with global_tracer.start_span(
                            "execute_tools_stream",
                            {"count": len(response.tool_calls)},
                        ):
                            async for tool_msg in process_stream_tool_calls(
                                response=response,
                                input_guardrails=input_guardrails,
                                output_guardrails=output_guardrails,
                                agent_name=self.name,
                                run_id=run_id,
                                step_num=step_num,
                                memory=self.memory,
                                execute_tool_fn=self._execute_tool,
                                stream_pub=_stream_pub,
                                tool_timeout=self.tool_timeout,
                            ):
                                yield tool_msg
        finally:
            self._current_run_id = ""

    # ── Private helpers ──────────────────────────────────────────────────────

    def _tool_needs_approval(self, tool_name: str) -> bool:
        """Check whether the given tool requires human approval."""
        return tool_needs_approval(tool_name, self.tools_requiring_approval)

    def _bootstrap_active_tools(self, current_input: str) -> None:
        """Seed the initial advertised tool subset from the user request."""
        actual_tools = [
            tool
            for tool in self.tools
            if getattr(tool, "name", None) != self._tool_search_name
        ]
        if len(actual_tools) <= self._max_active_tools:
            self._active_tool_names.update(tool.name for tool in actual_tools)
            return

        matches = self._catalog.search(
            current_input,
            limit=self._max_active_tools,
            kind_filter="tool",
            exclude_names={self._tool_search_name},
        )
        if matches:
            self._active_tool_names.update(entry.name for entry in matches)
            return

        for tool in actual_tools[: self._max_active_tools]:
            self._active_tool_names.add(tool.name)

    def _activate_tool_names(self, tool_names: List[str]) -> None:
        """Make searched or previously used tools available in later turns."""
        for tool_name in tool_names:
            if self._catalog.get_tool(tool_name) is not None:
                self._active_tool_names.add(tool_name)

    def _build_tool_schemas(self, current_input: str = "") -> List[JsonObject]:
        """Build tool schemas only for the currently advertised tool subset."""
        if not self._active_tool_names:
            self._bootstrap_active_tools(current_input)

        visible_names = self._always_visible_tool_names | self._active_tool_names
        schemas: List[JsonObject] = []
        for t in self.tools:
            tool_name = getattr(t, "name", None)
            if tool_name not in visible_names:
                continue
            if hasattr(t, "get_schema"):
                schema = t.get_schema()
                if hasattr(schema, "to_openai_format"):
                    schemas.append(schema.to_openai_format())
                elif isinstance(schema, dict):
                    schemas.append(schema)
            elif isinstance(t, dict):
                schemas.append(t)
        return schemas

    async def _call_llm(
        self,
        current_input: str = "",
        *,
        response_schema: Optional[type] = None,
        input_content: Optional[list[MediaType]] = None,
        **kwargs,
    ) -> AssistantMessage:
        """Single LLM call with retry, hooks, and observability."""
        tool_schemas = self._build_tool_schemas(current_input=current_input)
        requested_tool_choice = kwargs.pop("tool_choice", None)
        user_message_content = resolve_user_message_content(
            current_input,
            input_content,
        )
        memory_messages = await self.memory.get_messages()
        messages = await self.model_context.build(
            session_id=getattr(self, "_session_id", self.name),
            current_input=current_input,
            raw_messages=prepare_model_context_messages(
                memory_messages,
                current_input,
                user_message_content,
            ),
            model_client=self.model_client,
        )

        # ── LIFECYCLE HOOK: LLM_START ────────────────────────────────
        await self.hooks.dispatch(
            HookEvent.LLM_START,
            {
                "event": "on_llm_start",
                "agent_name": self.name,
                "message_count": len(messages),
                "tool_count": len(tool_schemas),
            },
        )

        with global_tracer.start_span("llm_generate", {"msg_count": len(messages)}):
            llm_t0 = asyncio.get_event_loop().time()
            last_exception: Optional[Exception] = None

            for attempt in range(self.llm_retry_policy.max_retries + 1):
                try:
                    # Strip SystemMessage entries from history — system instructions
                    # travel through the dedicated kwarg, not the conversation list.
                    # This prevents injected SystemMessage entries from overriding
                    # the agent's real instructions.
                    clean_messages = [
                        m for m in messages if not isinstance(m, SystemMessage)
                    ]
                    generate_kwargs: JsonObject = {
                        "messages": clean_messages,
                        "system_instructions": self.get_effective_system_prompt(),
                        "tools": tool_schemas or None,
                        "tool_choice": self._resolve_requested_tool_choice(
                            tool_schemas,
                            requested_tool_choice,
                        ),
                    }

                    # Wrap with middleware pipeline when middleware is configured
                    if self.middleware_pipeline.middleware:
                        metadata_bag = (
                            self.execution_context.inherited_metadata()
                            if self.execution_context is not None
                            else {}
                        )
                        metadata_bag["messages"] = list(clean_messages)

                        mw_ctx = MiddlewareContext(
                            stage=MiddlewareStage.LLM_CALL,
                            agent_name=self.name,
                            run_id=self._current_middleware_run_id(),
                            correlation_id=self._current_middleware_run_id(),
                            input_text=current_input,
                            response_schema=response_schema,
                            metadata=metadata_bag,
                            parent_context=self.execution_context,
                        )

                        async def _do_generate(
                            ctx: MiddlewareContext,
                        ) -> GenerateResult:
                            raw = ctx.metadata.get("messages", clean_messages)
                            generate_kwargs["messages"] = [
                                m for m in raw if not isinstance(m, SystemMessage)
                            ]
                            return await self.model_client.generate(**generate_kwargs)

                        response = await self.middleware_pipeline.run(
                            mw_ctx, _do_generate
                        )
                    else:
                        response = await self.model_client.generate(**generate_kwargs)

                    if not isinstance(response, AssistantMessage):
                        raise TypeError(
                            "ReActAgent expected AssistantMessage from generate()"
                        )

                    response = normalize_textual_tool_calls(response)
                    llm_t1 = asyncio.get_event_loop().time()
                    global_metrics.record_histogram(
                        "llm_latency",
                        llm_t1 - llm_t0,
                        tags={"model": getattr(self.model_client, "model", "unknown")},
                    )

                    # ── LIFECYCLE HOOK: LLM_END ──────────────────────
                    await self.hooks.dispatch(
                        HookEvent.LLM_END,
                        {
                            "event": "on_llm_end",
                            "agent_name": self.name,
                            "duration_ms": (llm_t1 - llm_t0) * 1000,
                            "usage": response.usage,
                            "has_tool_calls": bool(response.tool_calls),
                        },
                    )

                    return response

                except self.llm_retry_policy.retryable_exceptions as e:
                    last_exception = e
                    if attempt < self.llm_retry_policy.max_retries:
                        delay = _calculate_delay(attempt, self.llm_retry_policy)
                        logger.warning(
                            f"[{self.name}] LLM retry {attempt + 1}/"
                            f"{self.llm_retry_policy.max_retries}: {e} "
                            f"(waiting {delay:.1f}s)"
                        )
                        await asyncio.sleep(delay)
                    else:
                        global_metrics.increment_counter(
                            "llm_errors",
                            tags={"error": type(e).__name__},
                        )
                        raise

                except Exception as e:
                    global_metrics.increment_counter(
                        "llm_errors", tags={"error": type(e).__name__}
                    )
                    raise

        # Safety fallback (should never reach here)
        if last_exception:
            raise last_exception
        raise RuntimeError("LLM call failed unexpectedly")

    async def _execute_tool(
        self,
        parsed: ParsedToolCall,
        step_num: int,
    ) -> Tuple[ToolCallRecord, ToolExecutionResultMessage]:
        """Look up and execute a single tool call.

        Features: per-tool timeout, retry on transient errors, lifecycle hooks.
        Returns both the record (for AgentRunResult) and the message (for memory).
        """
        with global_tracer.start_span("tool_execution", {"tool": parsed.name}) as span:
            t0 = time.monotonic()

            # ── LIFECYCLE HOOK: TOOL_START ────────────────────────────
            await self.hooks.dispatch(
                HookEvent.TOOL_START,
                {
                    "event": "on_tool_start",
                    "agent_name": self.name,
                    "tool_name": parsed.name,
                    "arguments": parsed.arguments,
                    "step": step_num,
                },
            )

            # Look up tool from per-request catalog (correctly-wired instances)
            tool = find_tool(parsed.name, self._catalog, self.tools)

            # Build the shared execution context once per call
            exec_ctx = ToolExecutionContext(
                agent_name=self.name,
                run_id=self._current_middleware_run_id(),
                tool_timeout=self.tool_timeout,
                tool_retry_policy=self.tool_retry_policy,
                verbose=self.verbose,
                hooks=self.hooks,
                middleware_pipeline=self.middleware_pipeline,
                catalog=self._catalog,
                tools=self.tools,
                execution_context=self.execution_context,
                tool_search_name=self._tool_search_name,
                activate_tool_names_cb=self._activate_tool_names,
                skill_manager=self.skill_manager,
                runtime=self.runtime,
                agent_id=self.agent_id,
            )

            # ── RUNTIME DISPATCH PATH ────────────────────────────────
            # Only route through runtime when the tool declares a custom
            # agent_id for remote execution.  Default tools execute directly
            # from the per-request catalog — the runtime's ToolExecutorHandler
            # holds startup-time snapshots that lack per-request state
            # (e.g. AskHumanTool's bridge handler).
            if (
                self.runtime is not None
                and self.agent_id is not None
                and tool is not None
                and getattr(tool, "agent_id", None) is not None
            ):
                return await execute_tool_via_runtime(
                    parsed, step_num, t0, span, exec_ctx
                )

            # ── DIRECT EXECUTION PATH ────────────────────────────────

            if tool is None:
                result = build_tool_error(
                    parsed,
                    t0,
                    span,
                    f"Tool '{parsed.name}' not found in agent's tool list",
                    "tool_not_found_errors",
                    self.name,
                )
                await self.hooks.dispatch(
                    HookEvent.TOOL_END,
                    {
                        "event": "on_tool_end",
                        "agent_name": self.name,
                        "tool_name": parsed.name,
                        "is_error": True,
                        "error": "tool_not_found",
                        "duration_ms": (time.monotonic() - t0) * 1000,
                    },
                )
                return result

            if isinstance(tool, dict):
                result = build_tool_error(
                    parsed,
                    t0,
                    span,
                    f"Tool '{parsed.name}' is a raw dict schema, not executable. "
                    "Wrap with MCPTool.from_mcp_client().",
                    "tool_not_executable_errors",
                    self.name,
                )
                await self.hooks.dispatch(
                    HookEvent.TOOL_END,
                    {
                        "event": "on_tool_end",
                        "agent_name": self.name,
                        "tool_name": parsed.name,
                        "is_error": True,
                        "error": "tool_not_executable",
                        "duration_ms": (time.monotonic() - t0) * 1000,
                    },
                )
                return result

            # ── HITL: TOOL APPROVAL GATE ─────────────────────────
            if self.tool_approval_handler and self._tool_needs_approval(parsed.name):
                denial = await request_tool_approval(
                    parsed,
                    tool,
                    step_num,
                    t0,
                    span,
                    handler=self.tool_approval_handler,
                    hooks=self.hooks,
                    agent_name=self.name,
                )
                if denial is not None:
                    return denial

            # Execute with retry, timeout, and middleware
            return await execute_tool_direct(parsed, step_num, t0, span, tool, exec_ctx)

