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
import json
import time
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple
from uuid import uuid4

from opentelemetry.trace import Status, StatusCode

from agent_framework.core.agents.base_agent import BaseAgent
from agent_framework.core.agents.agent_result import (
    AgentRunResult,
    AggregatedUsage,
    RunStatus,
    StepResult,
    ToolCallRecord,
)
from agent_framework.core.context.base_context import ModelContext
from agent_framework.core.exceptions import GuardrailTripwireError
from agent_framework.core.guardrails.base_guardrail import (
    BaseGuardrail,
    GuardrailContext,
    GuardrailResult,
    GuardrailType,
)
from agent_framework.core.guardrails.runner import run_guardrails
from agent_framework.core.hooks import HookEvent, HookManager
from agent_framework.core.memory.base_memory import BaseMemory
from agent_framework.core.memory.memory_scope import MemoryScope
from agent_framework.core.memory.unbounded_memory import UnboundedMemory
from agent_framework.core.structured import StructuredOutputResult
from agent_framework.core.messages.client_messages import (
    AssistantMessage,
    SystemMessage,
    ToolCallMessage,
    ToolExecutionResultMessage,
    UserMessage,
)
from agent_framework.providers.llm.base_client import BaseModelClient
from agent_framework.runtime.observability import global_metrics, global_tracer, logger
from agent_framework.extensions.tools.human_input import (
    ToolApprovalAction,
    ToolApprovalHandler,
    ToolApprovalRequest,
    ToolApprovalResponse,
)
from agent_framework.core.resilience import (
    LLM_RETRY_POLICY,
    RetryPolicy,
    TOOL_RETRY_POLICY,
    _calculate_delay,
)
from agent_framework.core.tools.base_tool import BaseTool, HitlMode, ToolResult
from agent_framework.extensions.skills import SkillManager


# ---------------------------------------------------------------------------
# Helper: Parsed tool-call (normalised from any SDK shape)
# ---------------------------------------------------------------------------

class _ParsedToolCall:
    """Internal normalised representation of a tool call."""
    __slots__ = ("call_id", "name", "arguments")

    def __init__(self, call_id: str, name: str, arguments: Dict[str, Any]):
        self.call_id = call_id
        self.name = name
        self.arguments = arguments


# ---------------------------------------------------------------------------
# ReActAgent
# ---------------------------------------------------------------------------

class ReActAgent(BaseAgent):
    """Reasoning + Acting agent with tool calling loop.

    Usage::

        agent = ReActAgent(
            name="researcher",
            description="Answers questions using web tools",
            model_client=openai_client,
            tools=mcp_tools,
        )
        result = await agent.run("Find the top 3 repos for user X on GitHub")
        print(result.output)
        print(result.summary())
    """

    def __init__(
        self,
        name: str,
        description: str,
        *,
        model_client: BaseModelClient,
        tools: Optional[List[BaseTool]] = None,
        system_instructions: str = (
            "You are a helpful AI assistant. Use the provided tools to solve "
            "the user's request. Think step-by-step."
        ),
        memory: Optional[BaseMemory] = None,
        memory_scope: MemoryScope = MemoryScope.ISOLATED,
        model_context: ModelContext,
        max_iterations: int = 10,
        verbose: bool = True,
        input_guardrails: Optional[List[BaseGuardrail]] = None,
        output_guardrails: Optional[List[BaseGuardrail]] = None,
        # Production features
        hooks: Optional[HookManager] = None,
        llm_retry_policy: Optional[RetryPolicy] = None,
        tool_retry_policy: Optional[RetryPolicy] = None,
        run_timeout: Optional[float] = None,
        tool_timeout: Optional[float] = 30.0,
        # HITL: Tool approval
        tool_approval_handler: Optional[ToolApprovalHandler] = None,
        tools_requiring_approval: Optional[List[str]] = None,
        # Skills
        skill_dirs: Optional[List[str]] = None,
        skill_manager: Optional[SkillManager] = None,
    ):
        # Skills -- build SkillManager here (avoids core->extensions coupling in BaseAgent)
        _skill_manager: Optional[SkillManager] = skill_manager
        if _skill_manager is None and skill_dirs:
            from pathlib import Path
            _skill_manager = SkillManager(skill_dirs=[Path(d) for d in skill_dirs])

        super().__init__(
            name=name,
            description=description,
            model_client=model_client,
            model_context=model_context,
            tools=tools or [],
            system_instructions=system_instructions,
            memory=memory or UnboundedMemory(),
            memory_scope=memory_scope,
            input_guardrails=input_guardrails,
            output_guardrails=output_guardrails,
            prompt_enricher=_skill_manager,
        )
        # Keep skill_manager attribute for direct access / backwards compat
        self.skill_manager: Optional[SkillManager] = _skill_manager
        self.max_iterations = max_iterations
        self.verbose = verbose

        # Production features
        self.hooks = hooks or HookManager()
        self.llm_retry_policy = llm_retry_policy or LLM_RETRY_POLICY
        self.tool_retry_policy = tool_retry_policy or TOOL_RETRY_POLICY
        self.run_timeout = run_timeout  # None = no timeout
        self.tool_timeout = tool_timeout  # Per-tool timeout in seconds

        # HITL: tool approval
        self.tool_approval_handler = tool_approval_handler
        self.tools_requiring_approval = tools_requiring_approval  # None = all tools when handler set

    # ── Core run ─────────────────────────────────────────────────────────────

    async def _seed_system_message(self) -> None:
        """Seed the system prompt into memory if it is empty (lazy, async-safe)."""
        if await self.memory.size() == 0:
            await self.memory.add_message(SystemMessage(content=self.get_effective_system_prompt()))

    async def reset(self) -> None:
        """Clear memory and return agent to initial state with system message."""
        await super().reset()
        await self.memory.add_message(SystemMessage(content=self.get_effective_system_prompt()))
        # Reset HITL tool counters
        self._reset_hitl_tools()

    def _reset_hitl_tools(self) -> None:
        """Reset AskHumanTool request counters between runs."""
        from agent_framework.extensions.tools.human_input import AskHumanTool
        for tool in self.tools:
            if isinstance(tool, AskHumanTool):
                tool.reset()

    async def run(self, input_text: str, **kwargs) -> AgentRunResult:
        # Apply run-level timeout if configured
        if self.run_timeout:
            return await asyncio.wait_for(
                self._run_inner(input_text, **kwargs),
                timeout=self.run_timeout,
            )
        return await self._run_inner(input_text, **kwargs)

    async def _run_inner(self, input_text: str, **kwargs) -> AgentRunResult:
        run_id = str(uuid4())
        run_start = datetime.now(timezone.utc)
        usage = AggregatedUsage()
        steps: List[StepResult] = []
        tool_calls_by_name: Dict[str, int] = {}
        total_tool_calls = 0
        status = RunStatus.COMPLETED
        error_msg: Optional[str] = None
        final_output: List[Any] = []  # Multimodal output
        guardrail_results: List[GuardrailResult] = []

        attrs = {"agent_name": self.name, "input_length": len(input_text)}

        with global_tracer.start_span("agent_run", attrs) as run_span:
            global_metrics.increment_counter("agent_runs", tags={"name": self.name})
            if self.verbose:
                logger.info(f"[{self.name}] Starting run: {input_text[:80]}...")

            # ── LIFECYCLE HOOK: RUN_START ─────────────────────────────
            await self.hooks.dispatch(HookEvent.RUN_START, {
                "event": "on_run_start",
                "agent_name": self.name,
                "run_id": run_id,
                "input_text": input_text,
            })

            # Ensure system prompt is loaded (lazy seed for async memory)
            await self._seed_system_message()

            # 1. Add user message
            await self.memory.add_message(UserMessage(content=[input_text]))

            # ── INPUT GUARDRAILS ─────────────────────────────────────────
            try:
                if self.input_guardrails:
                    ctx = GuardrailContext(
                        agent_name=self.name,
                        run_id=run_id,
                        input_text=input_text,
                    )
                    results = await run_guardrails(
                        self.input_guardrails, ctx,
                        guardrail_type=GuardrailType.INPUT,
                    )
                    guardrail_results.extend(results)
            except GuardrailTripwireError as e:
                logger.error(f"[{self.name}] Input guardrail tripwire: {e.message}")
                run_end = datetime.now(timezone.utc)
                return AgentRunResult(
                    run_id=run_id,
                    agent_name=self.name,
                    output=[f"Request blocked: {e.message}"],
                    status=RunStatus.GUARDRAIL_TRIPPED,
                    steps=steps,
                    usage=usage,
                    start_time=run_start,
                    end_time=run_end,
                    duration_seconds=(run_end - run_start).total_seconds(),
                    max_iterations=self.max_iterations,
                    error=e.message,
                    guardrail_results=guardrail_results + (
                        [e.details["result"]] if "result" in e.details else []
                    ),
                )

            # 2. ReAct loop
            for step_num in range(1, self.max_iterations + 1):
                with global_tracer.start_span(f"step_{step_num}", {"step": step_num}):

                    # A. THINK — call LLM
                    response = await self._call_llm(current_input=input_text, **kwargs)
                    usage.add(response.usage)
                    await self.memory.add_message(response)

                    # Extract content (can be multimodal)
                    thought_content = response.content if response.content else None

                    # B. No tool calls → final answer
                    if not response.tool_calls:
                        if self.verbose:
                            logger.info(f"[{self.name}] Step {step_num}: final answer")
                        run_span.set_attribute("final_step", step_num)

                        # ── OUTPUT GUARDRAILS ────────────────────────────
                        output_text = self._extract_text(response)
                        try:
                            if self.output_guardrails:
                                ctx = GuardrailContext(
                                    agent_name=self.name,
                                    run_id=run_id,
                                    output_text=output_text,
                                    raw_message=response,
                                )
                                results = await run_guardrails(
                                    self.output_guardrails, ctx,
                                    guardrail_type=GuardrailType.OUTPUT,
                                )
                                guardrail_results.extend(results)
                        except GuardrailTripwireError as e:
                            logger.error(f"[{self.name}] Output guardrail tripwire: {e.message}")
                            run_end = datetime.now(timezone.utc)
                            return AgentRunResult(
                                run_id=run_id,
                                agent_name=self.name,
                                output=[f"Response blocked: {e.message}"],
                                status=RunStatus.GUARDRAIL_TRIPPED,
                                steps=steps,
                                usage=usage,
                                start_time=run_start,
                                end_time=run_end,
                                duration_seconds=(run_end - run_start).total_seconds(),
                                max_iterations=self.max_iterations,
                                error=e.message,
                                guardrail_results=guardrail_results + (
                                    [e.details["result"]] if "result" in e.details else []
                                ),
                            )

                        steps.append(StepResult(
                            step=step_num,
                            thought=thought_content,
                            tool_calls=[],
                            usage=response.usage,
                            finish_reason=response.finish_reason or "stop",
                        ))
                        final_output = thought_content or []
                        break

                    # C. ACT — execute tool calls
                    if self.verbose:
                        names = [self._parse_tool_call(tc).name for tc in response.tool_calls]
                        logger.info(f"[{self.name}] Step {step_num}: tool calls → {names}")

                    tool_records: List[ToolCallRecord] = []
                    for tc_raw in response.tool_calls:
                        parsed = self._parse_tool_call(tc_raw)

                        # ── TOOL-CALL GUARDRAILS ─────────────────────────
                        tool_blocked = False
                        try:
                            all_guardrails = self.input_guardrails + self.output_guardrails
                            tool_guardrails = [
                                g for g in all_guardrails
                                if g.guardrail_type == GuardrailType.TOOL_CALL
                            ]
                            if tool_guardrails:
                                ctx = GuardrailContext(
                                    agent_name=self.name,
                                    run_id=run_id,
                                    tool_name=parsed.name,
                                    tool_arguments=parsed.arguments,
                                )
                                results = await run_guardrails(
                                    tool_guardrails, ctx,
                                    guardrail_type=GuardrailType.TOOL_CALL,
                                )
                                guardrail_results.extend(results)
                        except GuardrailTripwireError as e:
                            logger.error(f"[{self.name}] Tool-call guardrail tripwire: {e.message}")
                            tool_blocked = True
                            # Create error tool message so the LLM sees it was blocked
                            tool_msg = ToolExecutionResultMessage(
                                content=[{"type": "text", "text": json.dumps({"error": f"Tool blocked: {e.message}"})}],
                                tool_call_id=parsed.call_id,
                                name=parsed.name,
                                isError=True,
                            )
                            await self.memory.add_message(tool_msg)
                            tool_records.append(ToolCallRecord(
                                tool_name=parsed.name,
                                call_id=parsed.call_id,
                                arguments=parsed.arguments,
                                result=f"Blocked by guardrail: {e.message}",
                                is_error=True,
                            ))
                            guardrail_results.extend(
                                [e.details["result"]] if "result" in e.details else []
                            )

                        if not tool_blocked:
                            record, tool_msg = await self._execute_tool(parsed, step_num)
                            await self.memory.add_message(tool_msg)
                            tool_records.append(record)

                        # Tally
                        tool_calls_by_name[parsed.name] = tool_calls_by_name.get(parsed.name, 0) + 1
                        total_tool_calls += 1

                    steps.append(StepResult(
                        step=step_num,
                        thought=thought_content,
                        tool_calls=tool_records,
                        usage=response.usage,
                        finish_reason="tool_calls",
                    ))

            else:
                # Loop exhausted without breaking → max iterations
                status = RunStatus.MAX_ITERATIONS
                if self.verbose:
                    logger.warning(f"[{self.name}] Hit max iterations ({self.max_iterations})")
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

            # ── LIFECYCLE HOOK: RUN_END ──────────────────────────────
            await self.hooks.dispatch(HookEvent.RUN_END, {
                "event": "on_run_end",
                "agent_name": self.name,
                "run_id": run_id,
                "status": status.value,
                "steps_used": len(steps),
                "tool_calls_total": total_tool_calls,
                "tokens_used": usage.total_tokens,
                "duration_seconds": duration,
            })

            return result

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

        Combines tool use, memory, and guardrails with a structured final
        extraction step.  The agent reasons freely (with tool calls if
        needed) for up to ``max_iterations`` steps, then one additional
        ``generate_structured()`` call is made with the accumulated
        conversation context so the final answer is validated against
        ``schema``.

        This lets you write deterministic workflows on top of a reasoning
        agent::

            class BookingDecision(BaseModel):
                approved: bool
                reason: str
                suggested_date: Optional[str] = None

            result = await agent.run_structured(
                'Book a flight from NYC to London for next Friday',
                schema=BookingDecision,
            )
            if result.ok:
                decision = result.parsed
                if decision.approved:
                    await confirm_booking(decision.suggested_date)

        Args:
            input_text: User input to process.
            schema: Pydantic ``BaseModel`` subclass for the final answer.
            max_iterations: Override the agent's default ``max_iterations``.
            **kwargs: Passed to the underlying ``_run_inner()`` call.

        Returns:
            ``StructuredOutputResult[schema]`` with the typed final answer.

        Raises:
            ``StructuredOutputError`` if the model cannot produce a valid
            structured output after the ReAct loop.
        """

        # Run the full ReAct loop (with tools, memory, guardrails)
        _saved_max = self.max_iterations
        if max_iterations is not None:
            self.max_iterations = max_iterations
        try:
            await self._run_inner(input_text, **kwargs)
        finally:
            self.max_iterations = _saved_max

        # Collect the conversation window built by the loop
        context_messages = await self.model_context.build(
            session_id=getattr(self, "_session_id", self.name),
            current_input=input_text,
            raw_messages=await self.memory.get_messages(),
        )

        # One final structured-output call to extract the typed answer
        structured_result: StructuredOutputResult = (
            await self.model_client.generate_structured(
                context_messages,
                schema,
                **{k: v for k, v in kwargs.items()
                   if k not in ("temperature", "tools", "tool_choice")},
            )
        )
        return structured_result

    # ── Streaming run ────────────────────────────────────────────────────────

    async def run_stream(self, input_text: str, **kwargs) -> AsyncIterator[Any]:
        """Streaming variant — yields partial chunks and tool results.

        Guardrails are applied at the same points as run():
          - Input guardrails: before first LLM call
          - Output guardrails: after final response (on CompletionChunk)
          - Tool-call guardrails: before each tool.execute()

        If an input guardrail trips, yields a single error message and returns.
        """
        run_id = str(uuid4())
        attrs = {"agent_name": self.name, "input_length": len(input_text)}
        with global_tracer.start_span("agent_run_stream", attrs):
            global_metrics.increment_counter("agent_runs", tags={"name": self.name})
            if self.verbose:
                logger.info(f"[{self.name}] Starting streaming run: {input_text[:80]}...")

            # Ensure system prompt is loaded (lazy seed for async memory)
            await self._seed_system_message()
            await self.memory.add_message(UserMessage(content=[input_text]))

            # ── INPUT GUARDRAILS ─────────────────────────────────────────
            try:
                if self.input_guardrails:
                    ctx = GuardrailContext(
                        agent_name=self.name,
                        run_id=run_id,
                        input_text=input_text,
                    )
                    await run_guardrails(
                        self.input_guardrails, ctx,
                        guardrail_type=GuardrailType.INPUT,
                    )
            except GuardrailTripwireError as e:
                logger.error(f"[{self.name}] Input guardrail tripwire: {e.message}")
                from agent_framework.core.messages._types import CompletionChunk
                yield CompletionChunk(
                    message=AssistantMessage(
                        role="assistant",
                        content=[f"Request blocked: {e.message}"],
                        finish_reason="guardrail_tripped",
                    ),
                    metadata={"guardrail_tripped": True, "guardrail": e.guardrail_name},
                )
                return

            for step_num in range(1, self.max_iterations + 1):
                with global_tracer.start_span(f"step_{step_num}", {"step": step_num}):
                    # THINK
                    tool_schemas = self._build_tool_schemas()
                    messages = await self.model_context.build(
                        session_id=getattr(self, "_session_id", self.name),
                        current_input=input_text,
                        raw_messages=await self.memory.get_messages(),
                        model_client=self.model_client,
                    )

                    with global_tracer.start_span("llm_generate_stream", {"msg_count": len(messages)}):
                        from agent_framework.core.messages._types import CompletionChunk, TextDeltaChunk
                        
                        llm_t0 = asyncio.get_event_loop().time()
                        final_response_obj = None
                        # Accumulate partial text so we can persist it if cancelled
                        # mid-stream before a CompletionChunk is received.
                        partial_text: str = ""

                        try:
                            async for chunk in self.model_client.generate_stream(
                                messages=messages,
                                tools=tool_schemas or None,
                                tool_choice="auto" if tool_schemas else None,
                                **kwargs,
                            ):
                                # Yield the chunk to user
                                yield chunk
                                
                                # Track partial text and final completion
                                if isinstance(chunk, TextDeltaChunk):
                                    partial_text += chunk.text
                                elif isinstance(chunk, CompletionChunk):
                                    final_response_obj = chunk.message
                            
                            # After stream completes, add final message to memory
                            if final_response_obj:
                                await self.memory.add_message(final_response_obj)
                            
                            llm_t1 = asyncio.get_event_loop().time()
                            global_metrics.record_histogram(
                                "llm_latency", llm_t1 - llm_t0,
                                tags={"model": getattr(self.model_client, "model", "unknown")},
                            )
                        except asyncio.CancelledError:
                            # Agent task was cancelled — persist whatever content we
                            # have so the conversation history stays coherent.
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
                            raise  # Must re-raise so the asyncio.Task is properly cancelled
                        except Exception as e:
                            global_metrics.increment_counter("llm_errors", tags={"error": type(e).__name__})
                            raise

                    # Use the final response from streaming (should always exist)
                    response = final_response_obj or AssistantMessage(
                        role="assistant",
                        content=None,
                    )

                    # No tool calls → done
                    if not response.tool_calls:
                        if self.verbose:
                            logger.info(f"[{self.name}] [stream] Step {step_num}: done")

                        # ── OUTPUT GUARDRAILS (stream) ───────────────────
                        try:
                            if self.output_guardrails:
                                output_text = self._extract_text(response)
                                ctx = GuardrailContext(
                                    agent_name=self.name,
                                    run_id=run_id,
                                    output_text=output_text,
                                    raw_message=response,
                                )
                                await run_guardrails(
                                    self.output_guardrails, ctx,
                                    guardrail_type=GuardrailType.OUTPUT,
                                )
                        except GuardrailTripwireError as e:
                            logger.error(f"[{self.name}] Output guardrail tripwire (stream): {e.message}")
                            yield CompletionChunk(
                                message=AssistantMessage(
                                    role="assistant",
                                    content=[f"Response blocked: {e.message}"],
                                    finish_reason="guardrail_tripped",
                                ),
                                metadata={"guardrail_tripped": True, "guardrail": e.guardrail_name},
                            )
                            return
                        break

                    # ACT — execute tools
                    if self.verbose:
                        names = [self._parse_tool_call(tc).name for tc in response.tool_calls]
                        logger.info(f"[{self.name}] [stream] Step {step_num}: tools → {names}")

                    with global_tracer.start_span("execute_tools_stream", {"count": len(response.tool_calls)}):
                        for tc_raw in response.tool_calls:
                            parsed = self._parse_tool_call(tc_raw)

                            # ── TOOL-CALL GUARDRAILS (stream) ────────────
                            tool_blocked = False
                            try:
                                all_guardrails = self.input_guardrails + self.output_guardrails
                                tool_guardrails = [
                                    g for g in all_guardrails
                                    if g.guardrail_type == GuardrailType.TOOL_CALL
                                ]
                                if tool_guardrails:
                                    ctx = GuardrailContext(
                                        agent_name=self.name,
                                        run_id=run_id,
                                        tool_name=parsed.name,
                                        tool_arguments=parsed.arguments,
                                    )
                                    await run_guardrails(
                                        tool_guardrails, ctx,
                                        guardrail_type=GuardrailType.TOOL_CALL,
                                    )
                            except GuardrailTripwireError as e:
                                logger.error(f"[{self.name}] Tool-call guardrail tripwire (stream): {e.message}")
                                tool_blocked = True
                                tool_msg = ToolExecutionResultMessage(
                                    content=[{"type": "text", "text": json.dumps({"error": f"Tool blocked: {e.message}"})}],
                                    tool_call_id=parsed.call_id,
                                    name=parsed.name,
                                    isError=True,
                                )
                                await self.memory.add_message(tool_msg)
                                yield tool_msg

                            if not tool_blocked:
                                _, tool_msg = await self._execute_tool(parsed, step_num)
                                await self.memory.add_message(tool_msg)
                                yield tool_msg

    # ── Private helpers ──────────────────────────────────────────────────────

    def _tool_needs_approval(self, tool_name: str) -> bool:
        """Check whether the given tool requires human approval."""
        if self.tools_requiring_approval is None:
            # Handler set but no explicit list → all tools need approval
            return True
        return tool_name in self.tools_requiring_approval

    def _build_tool_schemas(self) -> List[Dict[str, Any]]:
        """Build tool schemas for the LLM from the agent's tools list."""
        schemas: List[Dict[str, Any]] = []
        for t in self.tools:
            if hasattr(t, "get_schema"):
                schema = t.get_schema()
                if hasattr(schema, "to_openai_format"):
                    schemas.append(schema.to_openai_format())
                elif isinstance(schema, dict):
                    schemas.append(schema)
            elif isinstance(t, dict):
                schemas.append(t)
        return schemas

    async def _call_llm(self, current_input: str = "", **kwargs) -> AssistantMessage:
        """Single LLM call with retry, hooks, and observability."""
        tool_schemas = self._build_tool_schemas()
        messages = await self.model_context.build(
            session_id=getattr(self, "_session_id", self.name),
            current_input=current_input,
            raw_messages=await self.memory.get_messages(),
            model_client=self.model_client,
        )

        # ── LIFECYCLE HOOK: LLM_START ────────────────────────────────
        await self.hooks.dispatch(HookEvent.LLM_START, {
            "event": "on_llm_start",
            "agent_name": self.name,
            "message_count": len(messages),
            "tool_count": len(tool_schemas),
        })

        with global_tracer.start_span("llm_generate", {"msg_count": len(messages)}):
            llm_t0 = asyncio.get_event_loop().time()
            last_exception: Optional[Exception] = None

            for attempt in range(self.llm_retry_policy.max_retries + 1):
                try:
                    response = await self.model_client.generate(
                        messages=messages,
                        tools=tool_schemas or None,
                        tool_choice="auto" if tool_schemas else None,
                    )
                    llm_t1 = asyncio.get_event_loop().time()
                    global_metrics.record_histogram(
                        "llm_latency", llm_t1 - llm_t0,
                        tags={"model": getattr(self.model_client, "model", "unknown")},
                    )

                    # ── LIFECYCLE HOOK: LLM_END ──────────────────────
                    await self.hooks.dispatch(HookEvent.LLM_END, {
                        "event": "on_llm_end",
                        "agent_name": self.name,
                        "duration_ms": (llm_t1 - llm_t0) * 1000,
                        "usage": response.usage,
                        "has_tool_calls": bool(response.tool_calls),
                    })

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

    @staticmethod
    def _parse_tool_call(tc: Any) -> _ParsedToolCall:
        """Normalise any tool-call shape into a _ParsedToolCall.

        Handles: ToolCallMessage, OpenAI SDK objects with .function dict,
        raw dicts, and Pydantic ToolCall models.
        """
        call_id: Optional[str] = getattr(tc, "id", None)
        name: Optional[str] = None
        args: Any = None

        # 1. ToolCallMessage (our own type)
        if isinstance(tc, ToolCallMessage):
            return _ParsedToolCall(
                call_id=tc.id or str(uuid4()),
                name=tc.name,
                arguments=tc.arguments or {},
            )

        # 2. Object with .function dict (OpenAI SDK ChatCompletionMessageToolCall)
        if hasattr(tc, "function") and isinstance(getattr(tc, "function", None), dict):
            fn = tc.function
            name = fn.get("name")
            raw = fn.get("arguments")
            args = json.loads(raw) if isinstance(raw, str) else (raw or {})

        # 3. Plain dict
        elif isinstance(tc, dict):
            if "function" in tc and isinstance(tc["function"], dict):
                fn = tc["function"]
                name = fn.get("name")
                raw = fn.get("arguments")
                args = json.loads(raw) if isinstance(raw, str) else (raw or {})
            else:
                name = tc.get("name")
                args = tc.get("arguments", {})
                call_id = tc.get("id", call_id)

        # 4. Generic object with .name / .arguments
        elif hasattr(tc, "name") and hasattr(tc, "arguments"):
            name = tc.name
            args = tc.arguments if isinstance(tc.arguments, dict) else {}
            call_id = getattr(tc, "id", call_id)

        return _ParsedToolCall(
            call_id=call_id or str(uuid4()),
            name=name or "unknown",
            arguments=args if isinstance(args, dict) else {},
        )

    async def _execute_tool(
        self,
        parsed: _ParsedToolCall,
        step_num: int,
    ) -> Tuple[ToolCallRecord, ToolExecutionResultMessage]:
        """Look up and execute a single tool call.

        Features: per-tool timeout, retry on transient errors, lifecycle hooks.
        Returns both the record (for AgentRunResult) and the message (for memory).
        """
        with global_tracer.start_span("tool_execution", {"tool": parsed.name}) as span:
            t0 = time.monotonic()

            # ── LIFECYCLE HOOK: TOOL_START ────────────────────────────
            await self.hooks.dispatch(HookEvent.TOOL_START, {
                "event": "on_tool_start",
                "agent_name": self.name,
                "tool_name": parsed.name,
                "arguments": parsed.arguments,
                "step": step_num,
            })

            # Find tool
            tool = self._find_tool(parsed.name)

            if tool is None:
                result = self._tool_error(
                    parsed, step_num, t0, span,
                    f"Tool '{parsed.name}' not found in agent's tool list",
                    "tool_not_found_errors",
                )
                await self.hooks.dispatch(HookEvent.TOOL_END, {
                    "event": "on_tool_end",
                    "agent_name": self.name,
                    "tool_name": parsed.name,
                    "is_error": True,
                    "error": "tool_not_found",
                    "duration_ms": (time.monotonic() - t0) * 1000,
                })
                return result

            if isinstance(tool, dict):
                result = self._tool_error(
                    parsed, step_num, t0, span,
                    f"Tool '{parsed.name}' is a raw dict schema, not executable. "
                    "Wrap with MCPTool.from_mcp_client().",
                    "tool_not_executable_errors",
                )
                await self.hooks.dispatch(HookEvent.TOOL_END, {
                    "event": "on_tool_end",
                    "agent_name": self.name,
                    "tool_name": parsed.name,
                    "is_error": True,
                    "error": "tool_not_executable",
                    "duration_ms": (time.monotonic() - t0) * 1000,
                })
                return result

            # ── HITL: TOOL APPROVAL GATE ─────────────────────────
            if self.tool_approval_handler and self._tool_needs_approval(parsed.name):
                # Read the HITL mode declared on the tool instance
                _hitl_mode: HitlMode = getattr(tool, "hitl_mode", HitlMode.BLOCKING)
                approval_request = ToolApprovalRequest(
                    tool_name=parsed.name,
                    call_id=parsed.call_id,
                    arguments=parsed.arguments,
                    context=f"Agent wants to call '{parsed.name}' at step {step_num}",
                    hitl_mode=_hitl_mode.value if hasattr(_hitl_mode, "value") else str(_hitl_mode),
                    hitl_timeout_seconds=getattr(tool, "hitl_timeout_seconds", None),
                )
                try:
                    approval = await self.tool_approval_handler.request_approval(
                        approval_request
                    )
                except Exception as exc:
                    logger.error(f"[{self.name}] Approval handler error: {exc}")
                    approval = ToolApprovalResponse(
                        request_id=approval_request.request_id,
                        action=ToolApprovalAction.DENY,
                        reason=f"Approval handler error: {exc}",
                    )

                if approval.action == ToolApprovalAction.DENY:
                    deny_msg = approval.reason or "User denied tool execution"
                    logger.info(f"[{self.name}] Tool '{parsed.name}' DENIED: {deny_msg}")
                    result = self._tool_error(
                        parsed, step_num, t0, span,
                        f"Tool denied by user: {deny_msg}",
                        "tool_denied_by_user",
                    )
                    await self.hooks.dispatch(HookEvent.TOOL_END, {
                        "event": "on_tool_end",
                        "agent_name": self.name,
                        "tool_name": parsed.name,
                        "is_error": True,
                        "error": "denied_by_user",
                        "reason": deny_msg,
                        "duration_ms": (time.monotonic() - t0) * 1000,
                    })
                    return result

                if approval.action == ToolApprovalAction.MODIFY:
                    if approval.modified_arguments:
                        logger.info(
                            f"[{self.name}] Tool '{parsed.name}' MODIFIED: "
                            f"{parsed.arguments} → {approval.modified_arguments}"
                        )
                        parsed.arguments = approval.modified_arguments
                    else:
                        logger.info(f"[{self.name}] Tool '{parsed.name}' APPROVED (modify with no changes)")

                else:
                    logger.info(f"[{self.name}] Tool '{parsed.name}' APPROVED")

            # Execute with retry and timeout
            last_error: Optional[Exception] = None
            for attempt in range(self.tool_retry_policy.max_retries + 1):
                try:
                    if self.verbose:
                        logger.info(f"[{self.name}] Executing {parsed.name}({parsed.arguments})")

                    # Apply per-tool timeout
                    if self.tool_timeout:
                        exec_result: ToolResult = await asyncio.wait_for(
                            tool.execute(**parsed.arguments),
                            timeout=self.tool_timeout,
                        )
                    else:
                        exec_result = await tool.execute(**parsed.arguments)

                    duration_ms = (time.monotonic() - t0) * 1000

                    tool_msg = ToolExecutionResultMessage.from_tool_result(
                        tool_result=exec_result,
                        tool_call_id=parsed.call_id,
                        tool_name=parsed.name,
                    )
                    global_metrics.increment_counter("tool_executions", tags={"tool": parsed.name, "status": "success"})

                    record = ToolCallRecord(
                        tool_name=parsed.name,
                        call_id=parsed.call_id,
                        arguments=parsed.arguments,
                        result=self._content_to_str(tool_msg.content),
                        is_error=False,
                        duration_ms=duration_ms,
                    )

                    # ── LIFECYCLE HOOK: TOOL_END ─────────────────────
                    await self.hooks.dispatch(HookEvent.TOOL_END, {
                        "event": "on_tool_end",
                        "agent_name": self.name,
                        "tool_name": parsed.name,
                        "is_error": False,
                        "duration_ms": duration_ms,
                        "step": step_num,
                    })

                    return record, tool_msg

                except asyncio.TimeoutError:
                    last_error = TimeoutError(
                        f"Tool '{parsed.name}' timed out after {self.tool_timeout}s"
                    )
                    if attempt < self.tool_retry_policy.max_retries:
                        delay = _calculate_delay(attempt, self.tool_retry_policy)
                        logger.warning(
                            f"[{self.name}] Tool timeout, retry "
                            f"{attempt + 1}/{self.tool_retry_policy.max_retries} "
                            f"(waiting {delay:.1f}s)"
                        )
                        await asyncio.sleep(delay)
                        continue

                except self.tool_retry_policy.retryable_exceptions as e:
                    last_error = e
                    if attempt < self.tool_retry_policy.max_retries:
                        delay = _calculate_delay(attempt, self.tool_retry_policy)
                        logger.warning(
                            f"[{self.name}] Tool retry {attempt + 1}/"
                            f"{self.tool_retry_policy.max_retries}: {e} "
                            f"(waiting {delay:.1f}s)"
                        )
                        await asyncio.sleep(delay)
                        continue

                except Exception as e:
                    last_error = e
                    break  # Non-retryable — do not keep trying

            # All retries exhausted
            error_msg = str(last_error) if last_error else "Unknown tool error"
            result = self._tool_error(
                parsed, step_num, t0, span,
                error_msg, "tool_execution_errors",
            )
            await self.hooks.dispatch(HookEvent.TOOL_END, {
                "event": "on_tool_end",
                "agent_name": self.name,
                "tool_name": parsed.name,
                "is_error": True,
                "error": error_msg,
                "duration_ms": (time.monotonic() - t0) * 1000,
            })
            return result

    def _tool_error(
        self,
        parsed: _ParsedToolCall,
        step_num: int,
        t0: float,
        span: Any,
        error_msg: str,
        metric_name: str,
    ) -> Tuple[ToolCallRecord, ToolExecutionResultMessage]:
        """Build error record + message for a failed tool call."""
        duration_ms = (time.monotonic() - t0) * 1000
        logger.error(f"[{self.name}] {error_msg}")
        span.set_status(Status(StatusCode.ERROR))
        global_metrics.increment_counter(metric_name, tags={"tool": parsed.name})

        tool_msg = ToolExecutionResultMessage(
            content=[{"type": "text", "text": json.dumps({"error": error_msg})}],
            tool_call_id=parsed.call_id,
            name=parsed.name,
            isError=True,
        )
        record = ToolCallRecord(
            tool_name=parsed.name,
            call_id=parsed.call_id,
            arguments=parsed.arguments,
            result=error_msg,
            is_error=True,
            duration_ms=duration_ms,
        )
        return record, tool_msg

    def _find_tool(self, name: str) -> Optional[Any]:
        """Look up a tool by name from the agent's tools list."""
        for t in self.tools:
            t_name = getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else None)
            if t_name == name:
                return t
        return None

    @staticmethod
    def _extract_text(response: AssistantMessage) -> Optional[str]:
        """Extract plain text content from an AssistantMessage."""
        if response.content is None:
            return None
        if isinstance(response.content, list):
            parts = [str(c) for c in response.content if c]
            return " ".join(parts) if parts else None
        return str(response.content) if response.content else None

    @staticmethod
    def _content_to_str(content: Any) -> str:
        """Convert tool result content to a plain string for the record."""
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
                else:
                    parts.append(str(block))
            return "\n".join(parts)
        return str(content) if content else ""
