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


from ravi.core.agents.base_agent import BaseAgent
from ravi.core.execution.context import ExecutionContext
from ravi.core.runtime._protocol import AgentId, AgentRuntime
from ravi.core.agents.agent_result import (
    AgentRunResult,
    AggregatedUsage,
    RunStatus,
    StepResult,
    ToolCallRecord,
)
from ravi.core.agents._guardrail_runner import (
    build_guardrail_tripped_result,
    build_tool_blocked_message,
    build_tool_blocked_record,
    check_input_guardrails,
    check_output_guardrails,
    check_tool_call_guardrails,
)
from ravi.core.agents._tool_execution import (
    ParsedToolCall,
    build_tool_error,
    content_to_str,
    execute_tool_direct,
    execute_tool_via_runtime,
    find_tool,
    parse_tool_call,
    request_tool_approval,
    tool_needs_approval,
)
from ravi.core.agents._stream_handler import (
    handle_stream_final_response,
    process_stream_tool_calls,
)
from ravi.core.context.base_context import ModelContext
from ravi.exceptions import GuardrailTripwireError
from ravi.core.guardrails.base_guardrail import (
    BaseGuardrail,
    GuardrailResult,
)
from ravi.core.hooks import HookEvent, HookManager
from ravi.core.memory.base_memory import BaseMemory
from ravi.core.memory.memory_scope import MemoryScope
from ravi.core.memory.unbounded_memory import UnboundedMemory
from ravi.core.messages.base_message import BaseClientMessage
from ravi.core.middleware.base import (
    BaseMiddleware,
    MiddlewareContext,
    MiddlewareStage,
)
from ravi.core.structured import StructuredOutputResult
from ravi.core.messages.client_messages import (
    AssistantMessage,
    SystemMessage,
    ToolCallMessage,
    ToolExecutionResultMessage,
    UserMessage,
)
from ravi.core.messages._types import MediaType
from ravi.core.llm.base_client import BaseModelClient
from ravi.shared.observability import global_metrics, global_tracer, logger
from ravi.catalog.tools.human_input.tool import (
    ToolApprovalHandler,
)
from ravi.core.resilience import (
    LLM_RETRY_POLICY,
    RetryPolicy,
    TOOL_RETRY_POLICY,
    _calculate_delay,
)
from ravi.core.tools.base_tool import BaseTool
from ravi.core.tools.catalog import CapabilityRegistry
from ravi.catalog import SkillManager
from ravi.catalog.tools.capability_search.tool import CapabilitySearchTool


# ---------------------------------------------------------------------------
# Backward-compat alias — ParsedToolCall now lives in _tool_execution.py
# ---------------------------------------------------------------------------

_ParsedToolCall = ParsedToolCall


def _resolve_user_message_content(
    input_text: str,
    input_content: Any,
) -> list[MediaType]:
    if input_content is None:
        return [input_text]
    if not isinstance(input_content, list):
        raise ValueError("input_content must be a list of media items")
    if input_content:
        return input_content
    return [input_text]


def _has_non_text_media(content: list[MediaType]) -> bool:
    return any(not isinstance(item, str) for item in content)


def _build_persisted_user_message(
    input_text: str,
    content: list[MediaType],
) -> UserMessage:
    if _has_non_text_media(content):
        return UserMessage(content=[input_text])
    return UserMessage(content=content)


def _inject_ephemeral_user_message(
    raw_messages: list[BaseClientMessage],
    input_text: str,
    ephemeral_content: list[MediaType],
) -> list[BaseClientMessage]:
    if not _has_non_text_media(ephemeral_content):
        return raw_messages

    expected_text = input_text.strip()
    patched_messages = list(raw_messages)

    for index in range(len(patched_messages) - 1, -1, -1):
        candidate = patched_messages[index]
        if not isinstance(candidate, UserMessage):
            continue
        if any(not isinstance(item, str) for item in candidate.content):
            continue

        text_parts = [
            item for item in candidate.content if isinstance(item, str) and item
        ]
        candidate_text = "\n".join(text_parts).strip()
        if candidate_text != expected_text:
            continue

        patched_messages[index] = UserMessage(
            content=ephemeral_content,
            name=candidate.name,
        )
        break

    return patched_messages


def _sanitize_message_for_model_context(
    message: BaseClientMessage,
) -> BaseClientMessage:
    if not isinstance(message, UserMessage):
        return message
    if not _has_non_text_media(message.content):
        return message

    text_parts = [
        item for item in message.content if isinstance(item, str) and item.strip()
    ]
    sanitized_text = "\n".join(text_parts).strip()
    if not sanitized_text:
        sanitized_text = "[User provided a non-text attachment in a previous turn.]"

    return UserMessage(content=[sanitized_text], name=message.name)


def _prepare_model_context_messages(
    raw_messages: list[BaseClientMessage],
    input_text: str,
    ephemeral_content: list[MediaType],
) -> list[BaseClientMessage]:
    sanitized_messages = [
        _sanitize_message_for_model_context(message) for message in raw_messages
    ]
    return _inject_ephemeral_user_message(
        sanitized_messages,
        input_text,
        ephemeral_content,
    )


def _assistant_text_parts(content: Optional[List[MediaType]]) -> list[str]:
    parts: list[str] = []
    if not content:
        return parts

    for item in content:
        if isinstance(item, str):
            if item:
                parts.append(item)
            continue

        if isinstance(item, dict):
            text = item.get("text")
            if isinstance(text, str) and text:
                parts.append(text)

    return parts


def _parse_textual_tool_call_sequence(text: str) -> list[ToolCallMessage]:
    remaining = text.strip()
    parsed_calls: list[ToolCallMessage] = []

    while remaining:
        # Accept both Groq format (<function=name{...}) and legacy (<function/name{...)
        if remaining.startswith("<function="):
            prefix_len = len("<function=")
        elif remaining.startswith("<function/"):
            prefix_len = len("<function/")
        else:
            return []

        close_tag_index = remaining.find("</function>")
        self_closing_index = remaining.find("/>")

        if close_tag_index == -1 and self_closing_index == -1:
            return []

        is_self_closing = self_closing_index != -1 and (
            close_tag_index == -1 or self_closing_index < close_tag_index
        )
        end_index = self_closing_index if is_self_closing else close_tag_index

        inner = remaining[prefix_len:end_index].strip()

        tool_name = ""
        raw_arguments = ""

        open_brace_index = inner.find("{")
        if open_brace_index > 0:
            tool_name = inner[:open_brace_index].strip().rstrip(">")
            raw_arguments = inner[open_brace_index:].strip()
        else:
            comma_index = inner.find(",")
            if comma_index <= 0:
                return []
            tool_name = inner[:comma_index].strip()
            raw_arguments = inner[comma_index + 1 :].strip()

        if not tool_name or not raw_arguments:
            return []

        if raw_arguments.endswith(">"):
            raw_arguments = raw_arguments[:-1].strip()

        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError:
            return []

        if not isinstance(arguments, dict):
            return []

        parsed_calls.append(ToolCallMessage(name=tool_name, arguments=arguments))
        closing_len = len("/>") if is_self_closing else len("</function>")
        remaining = remaining[end_index + closing_len :].strip()

    return parsed_calls


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

    DEFAULT_MAX_ACTIVE_TOOLS = 8

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
        max_iterations: int = 50,
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
        # Structured output: when set, run() / run_stream() parse the final
        # answer into this Pydantic model.
        response_schema: Optional[type] = None,
        # Middleware: opt-in composable pipeline for pre/post processing.
        middleware: Optional[List[BaseMiddleware]] = None,
        execution_context: Optional[ExecutionContext] = None,
        # Runtime
        runtime: Optional[AgentRuntime] = None,
        agent_id: Optional[AgentId] = None,
        enable_capability_search: bool = True,
    ):
        provided_tools = list(tools or [])

        # Skills -- build SkillManager here (avoids core->extensions coupling in BaseAgent)
        _skill_manager: Optional[SkillManager] = skill_manager
        if _skill_manager is None and skill_dirs:
            from pathlib import Path

            _skill_manager = SkillManager(skill_dirs=[Path(d) for d in skill_dirs])

        # Build the unified capability catalog from tools + skills
        self._catalog = CapabilityRegistry()
        for tool in provided_tools:
            self._catalog.register_tool(tool)
        if _skill_manager is not None:
            for meta in _skill_manager._discovered:
                self._catalog.register_skill(meta)

        # Inject the capability search tool (replaces old ToolSearchTool)
        existing_search = self._catalog.get_tool("capability_search")
        if enable_capability_search and existing_search is None:
            search_tool = CapabilitySearchTool(self._catalog)
            provided_tools.append(search_tool)
            self._catalog.register_tool(search_tool)

        # Resolve memory before super().__init__ so we can narrow the type below.
        _resolved_memory: BaseMemory = memory or UnboundedMemory()

        super().__init__(
            name=name,
            description=description,
            model_client=model_client,
            model_context=model_context,
            tools=provided_tools,
            system_instructions=system_instructions,
            memory=_resolved_memory,
            memory_scope=memory_scope,
            input_guardrails=input_guardrails,
            output_guardrails=output_guardrails,
            prompt_enricher=_skill_manager,
            response_schema=response_schema,
            middleware=middleware,
            execution_context=execution_context,
            runtime=runtime,
            agent_id=agent_id,
        )
        # Narrow type: self.memory is always non-None in ReActAgent.
        self.memory: BaseMemory = _resolved_memory
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
        self.tools_requiring_approval = (
            tools_requiring_approval  # None = all tools when handler set
        )
        self._active_tool_names: set[str] = set()
        self._tool_search_name = "capability_search"
        self._always_visible_tool_names = {
            name
            for name in (self._tool_search_name, "ask_human")
            if self._catalog.get_tool(name) is not None
        }
        self._max_active_tools = self.DEFAULT_MAX_ACTIVE_TOOLS

    # ── Core run ─────────────────────────────────────────────────────────────

    async def _seed_system_message(self) -> None:
        """Seed the system prompt into memory if it is empty (lazy, async-safe)."""
        if await self.memory.size() == 0:
            await self.memory.add_message(
                SystemMessage(content=self.get_effective_system_prompt())
            )

    async def reset(self) -> None:
        """Clear memory and return agent to initial state with system message."""
        await super().reset()
        self._reset_tool_activation_state()
        await self.memory.add_message(
            SystemMessage(content=self.get_effective_system_prompt())
        )
        # Reset HITL tool counters
        self._reset_hitl_tools()

    def _reset_hitl_tools(self) -> None:
        """Reset AskHumanTool request counters between runs."""
        from ravi.catalog.tools.human_input.tool import AskHumanTool

        for tool in self.tools:
            if isinstance(tool, AskHumanTool):
                tool.reset()

    def _reset_tool_activation_state(self) -> None:
        """Clear the currently advertised tool subset between runs."""
        self._active_tool_names.clear()

    def _resolve_run_id(self) -> str:
        """Return the active execution run id or create a new one."""
        if self.execution_context is not None and self.execution_context.run_id:
            return self.execution_context.run_id
        return str(uuid4())

    @staticmethod
    def _resolve_requested_tool_choice(
        tool_schemas: List[Dict[str, Any]],
        requested_tool_choice: Optional[str | dict[str, Any]] = None,
    ) -> Optional[str | dict[str, Any]]:
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
        final_output: List[Any] = []  # Multimodal output
        guardrail_results: List[GuardrailResult] = []
        response: Optional[AssistantMessage] = None
        initial_tool_choice = kwargs.pop("tool_choice", None)

        attrs = {"agent_name": self.name, "input_length": len(input_text)}
        user_message_content = _resolve_user_message_content(
            input_text,
            kwargs.pop("input_content", None),
        )
        persisted_user_message = _build_persisted_user_message(
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

                # Ensure system prompt is loaded (lazy seed for async memory)
                await self._seed_system_message()

                # 1. Add user message
                await self.memory.add_message(persisted_user_message)

                # ── INPUT GUARDRAILS ─────────────────────────────────────────
                try:
                    if self.input_guardrails:
                        results = await check_input_guardrails(
                            guardrails=self.input_guardrails,
                            agent_name=self.name,
                            run_id=run_id,
                            input_text=input_text,
                        )
                        guardrail_results.extend(results)
                except GuardrailTripwireError as e:
                    logger.error(f"[{self.name}] Input guardrail tripwire: {e.message}")
                    return build_guardrail_tripped_result(
                        error=e,
                        run_id=run_id,
                        agent_name=self.name,
                        run_start=run_start,
                        steps=steps,
                        usage=usage,
                        max_iterations=self.max_iterations,
                        guardrail_results=guardrail_results,
                        output_prefix="Request blocked",
                    )

                # 2. ReAct loop
                for step_num in range(1, self.max_iterations + 1):
                    with global_tracer.start_span(
                        f"step_{step_num}", {"step": step_num}
                    ):
                        # A. THINK — call LLM
                        response = await self._call_llm(
                            current_input=input_text,
                            response_schema=response_schema,
                            input_content=user_message_content,
                            tool_choice=(
                                initial_tool_choice if step_num == 1 else None
                            ),
                            **kwargs,
                        )
                        usage.add(response.usage)
                        await self.memory.add_message(response)

                        thought_content = response.content if response.content else None

                        if not response.tool_calls:
                            if self.verbose:
                                logger.info(
                                    f"[{self.name}] Step {step_num}: final answer"
                                )
                            run_span.set_attribute("final_step", step_num)

                            output_text = self._extract_text(response)
                            try:
                                if self.output_guardrails:
                                    og_results = await check_output_guardrails(
                                        guardrails=self.output_guardrails,
                                        agent_name=self.name,
                                        run_id=run_id,
                                        output_text=output_text,
                                        raw_message=response,
                                    )
                                    guardrail_results.extend(og_results)
                            except GuardrailTripwireError as e:
                                logger.error(
                                    f"[{self.name}] Output guardrail tripwire: {e.message}"
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
                                    output_prefix="Response blocked",
                                )

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

                            tool_blocked = False
                            try:
                                tc_results = await check_tool_call_guardrails(
                                    input_guardrails=self.input_guardrails,
                                    output_guardrails=self.output_guardrails,
                                    agent_name=self.name,
                                    run_id=run_id,
                                    parsed=parsed,
                                )
                                guardrail_results.extend(tc_results)
                            except GuardrailTripwireError as e:
                                logger.error(
                                    f"[{self.name}] Tool-call guardrail tripwire: {e.message}"
                                )
                                tool_blocked = True
                                tool_msg = build_tool_blocked_message(parsed, e.message)
                                await self.memory.add_message(tool_msg)
                                tool_records.append(
                                    build_tool_blocked_record(parsed, e.message)
                                )
                                guardrail_results.extend(
                                    [e.details["result"]]
                                    if "result" in e.details
                                    else []
                                )

                            if not tool_blocked:
                                record, tool_msg = await self._execute_tool(
                                    parsed, step_num
                                )
                                await self.memory.add_message(tool_msg)
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
                        from ravi.core.structured.result import StructuredOutputResult

                        assert response is not None
                        raw_text = self._extract_text(response) or ""
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
                            raw_messages=_prepare_model_context_messages(
                                memory_messages,
                                input_text,
                                user_message_content,
                            ),
                            model_client=self.model_client,
                        )
                        result.structured_output = await self.model_client.generate(
                            context_messages,
                            response_format=response_schema,
                        )

                # ── LIFECYCLE HOOK: RUN_END ──────────────────────────────
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

                return result
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
            from ravi.core.structured import StructuredOutputError

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
    ) -> AsyncIterator[Any]:
        """Streaming variant — yields partial chunks and tool results.

        Guardrails are applied at the same points as run():
          - Input guardrails: before first LLM call
          - Output guardrails: after final response (on CompletionChunk)
          - Tool-call guardrails: before each tool.execute()

        If an input guardrail trips, yields a single error message and returns.
        """
        self._reset_tool_activation_state()
        run_id = self._resolve_run_id()
        self._current_run_id = run_id
        attrs = {"agent_name": self.name, "input_length": len(input_text)}
        user_message_content = _resolve_user_message_content(
            input_text,
            kwargs.pop("input_content", None),
        )
        persisted_user_message = _build_persisted_user_message(
            input_text,
            user_message_content,
        )
        initial_tool_choice = kwargs.pop("tool_choice", None)

        # Optional: publish chunks to a topic for remote subscribers
        _stream_pub = None
        if self.runtime is not None and self.agent_id is not None:
            from ravi.core.runtime._stream import StreamPublisher
            from ravi.core.runtime._protocol import TopicId

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

                # Ensure system prompt is loaded (lazy seed for async memory)
                await self._seed_system_message()
                await self.memory.add_message(persisted_user_message)

                # ── INPUT GUARDRAILS ─────────────────────────────────────────
                try:
                    if self.input_guardrails:
                        await check_input_guardrails(
                            guardrails=self.input_guardrails,
                            agent_name=self.name,
                            run_id=run_id,
                            input_text=input_text,
                        )
                except GuardrailTripwireError as e:
                    logger.error(f"[{self.name}] Input guardrail tripwire: {e.message}")
                    from ravi.core.messages._types import CompletionChunk

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
                            raw_messages=_prepare_model_context_messages(
                                memory_messages,
                                input_text,
                                user_message_content,
                            ),
                            model_client=self.model_client,
                        )

                        with global_tracer.start_span(
                            "llm_generate_stream", {"msg_count": len(messages)}
                        ):
                            from ravi.core.messages._types import (
                                CompletionChunk,
                                TextDeltaChunk,
                            )

                            llm_t0 = asyncio.get_event_loop().time()
                            final_response_obj = None
                            # Accumulate partial text so we can persist it if cancelled
                            # mid-stream before a CompletionChunk is received.
                            partial_text: str = ""

                            try:
                                async for chunk in self.model_client.generate_stream(
                                    messages=messages,
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
                                        self._normalize_textual_tool_calls(
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
                                output_guardrails=self.output_guardrails,
                                agent_name=self.name,
                                run_id=run_id,
                                model_client=self.model_client,
                                model_context=self.model_context,
                                memory=self.memory,
                                input_text=input_text,
                                response_schema=_schema,
                                stream_pub=_stream_pub,
                                extract_text_fn=self._extract_text,
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
                                input_guardrails=self.input_guardrails,
                                output_guardrails=self.output_guardrails,
                                agent_name=self.name,
                                run_id=run_id,
                                step_num=step_num,
                                memory=self.memory,
                                execute_tool_fn=self._execute_tool,
                                stream_pub=_stream_pub,
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

    def _build_tool_schemas(self, current_input: str = "") -> List[Dict[str, Any]]:
        """Build tool schemas only for the currently advertised tool subset."""
        if not self._active_tool_names:
            self._bootstrap_active_tools(current_input)

        visible_names = self._always_visible_tool_names | self._active_tool_names
        schemas: List[Dict[str, Any]] = []
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
        input_content: Any = None,
        **kwargs,
    ) -> AssistantMessage:
        """Single LLM call with retry, hooks, and observability."""
        tool_schemas = self._build_tool_schemas(current_input=current_input)
        requested_tool_choice = kwargs.pop("tool_choice", None)
        user_message_content = _resolve_user_message_content(
            current_input,
            input_content,
        )
        memory_messages = await self.memory.get_messages()
        messages = await self.model_context.build(
            session_id=getattr(self, "_session_id", self.name),
            current_input=current_input,
            raw_messages=_prepare_model_context_messages(
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
                    generate_kwargs: dict[str, Any] = {
                        "messages": messages,
                        "tools": tool_schemas or None,
                        "tool_choice": self._resolve_requested_tool_choice(
                            tool_schemas,
                            requested_tool_choice,
                        ),
                    }

                    # Wrap with middleware pipeline when middleware is configured
                    if self.middleware_pipeline.middleware:
                        mw_ctx = MiddlewareContext(
                            stage=MiddlewareStage.LLM_CALL,
                            agent_name=self.name,
                            run_id=self._current_middleware_run_id(),
                            correlation_id=self._current_middleware_run_id(),
                            input_text=current_input,
                            response_schema=response_schema,
                            metadata=(
                                self.execution_context.inherited_metadata()
                                if self.execution_context is not None
                                else {}
                            ),
                            parent_context=self.execution_context,
                        )

                        async def _do_generate(
                            ctx: MiddlewareContext,
                        ) -> Any:
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

                    response = self._normalize_textual_tool_calls(response)
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

    @staticmethod
    def _parse_tool_call(tc: Any) -> ParsedToolCall:
        """Normalise any tool-call shape into a ParsedToolCall.

        Delegates to ``_tool_execution.parse_tool_call``.
        """
        return parse_tool_call(tc)

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
                    parsed,
                    step_num,
                    t0,
                    span,
                    runtime=self.runtime,
                    agent_id=self.agent_id,
                    agent_name=self.name,
                    hooks=self.hooks,
                    catalog=self._catalog,
                    tools=self.tools,
                    activate_tool_names_cb=self._activate_tool_names,
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
            return await execute_tool_direct(
                parsed,
                step_num,
                t0,
                span,
                tool=tool,
                agent_name=self.name,
                verbose=self.verbose,
                tool_timeout=self.tool_timeout,
                tool_retry_policy=self.tool_retry_policy,
                hooks=self.hooks,
                middleware_pipeline=self.middleware_pipeline,
                execution_context=self.execution_context,
                run_id=self._current_middleware_run_id(),
                tool_search_name=self._tool_search_name,
                activate_tool_names_cb=self._activate_tool_names,
                skill_manager=self.skill_manager,
            )

    def _tool_error(
        self,
        parsed: ParsedToolCall,
        step_num: int,
        t0: float,
        span: Any,
        error_msg: str,
        metric_name: str,
    ) -> Tuple[ToolCallRecord, ToolExecutionResultMessage]:
        """Build error record + message for a failed tool call."""
        return build_tool_error(parsed, t0, span, error_msg, metric_name, self.name)

    async def _execute_tool_via_runtime(
        self,
        parsed: ParsedToolCall,
        step_num: int,
        t0: float,
        span: Any,
    ) -> Tuple[ToolCallRecord, ToolExecutionResultMessage]:
        """Dispatch tool execution through the agent runtime."""
        assert self.runtime is not None
        assert self.agent_id is not None
        return await execute_tool_via_runtime(
            parsed,
            step_num,
            t0,
            span,
            runtime=self.runtime,
            agent_id=self.agent_id,
            agent_name=self.name,
            hooks=self.hooks,
            catalog=self._catalog,
            tools=self.tools,
            activate_tool_names_cb=self._activate_tool_names,
        )

    def _find_tool(self, name: str) -> Optional[Any]:
        """Look up a tool by name (or alias) from the catalog."""
        return find_tool(name, self._catalog, self.tools)

    @staticmethod
    def _extract_text(response: AssistantMessage) -> Optional[str]:
        """Extract plain text content from an AssistantMessage."""
        if response.content is None:
            return None
        if isinstance(response.content, list):
            parts = _assistant_text_parts(response.content)
            return " ".join(parts) if parts else None
        return str(response.content) if response.content else None

    @staticmethod
    def _normalize_textual_tool_calls(response: AssistantMessage) -> AssistantMessage:
        """Translate fallback textual tool-call markup into ToolCallMessage objects."""
        if response.tool_calls:
            return response

        text = ReActAgent._extract_text(response)
        if not text:
            return response

        parsed_calls = _parse_textual_tool_call_sequence(text)
        if not parsed_calls:
            return response

        response.tool_calls = parsed_calls
        response.content = None
        response.finish_reason = "tool_calls"
        return response

    @staticmethod
    def _content_to_str(content: Any) -> str:
        """Convert tool result content to a plain string for the record."""
        return content_to_str(content)
