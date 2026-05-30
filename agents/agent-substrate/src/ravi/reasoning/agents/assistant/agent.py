"""AssistantAgent — full cognitive actor replacing ReActAgent.

Every agent is an actor registered with a runtime.  AssistantAgent merges the
complete ReAct loop (streaming, guardrails, HITL, checkpointing,
lineage, observability) into the actor model.

Entry point is always ``on_message()``, never ``run()`` directly.
External callers use ``UserProxyAgent.ask()`` / ``ask_stream()``.

Streaming pattern::

    # chat.py (server layer creates the channel adapter):
    channel = _TranslatingChannel(EventBus())
    envelope = StreamEnvelope(task="...", channel=channel)
    asyncio.create_task(runtime.send_message(envelope, recipient=agent.id))
    async for event in channel.bus:
        yield f"data: {event.to_sse()}\\n\\n"
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, AsyncIterator, Dict, List, Optional, Tuple, Union

if TYPE_CHECKING:
    from ravi.kernel.llm.base_client import BaseModelClient
from uuid import uuid4

from ravi.kernel.messages.content import JsonObject
from ravi.kernel.messages._types import StreamChunk

from ravi.fabric.actors.actor import ActorAgent, StreamEnvelope, StreamChannel
from ravi.kernel.messages.content import ContentBlock
from ravi.kernel.runtime._message import MessageContext
from ravi.kernel.plugin import register_agent
from ravi.reasoning.agents.assistant._react_loop import (
    build_persisted_user_message,
    extract_text,
    normalize_textual_tool_calls,
    prepare_model_context_messages,
    resolve_user_message_content,
)
from ravi.kernel.execution.context import ExecutionContext
from ravi.kernel.runtime import AgentRuntime, StreamPublisher, TopicId
from ravi.fabric.agents_base.agent_result import (
    AgentRunResult,
    AggregatedUsage,
    RunStatus,
    StepResult,
    ToolCallRecord,
)
from ravi.reasoning.agents.assistant._guardrail_runner import (
    build_guardrail_tripped_result,
    build_tool_blocked_message,
    build_tool_blocked_record,
    check_input_guardrails,
    check_tool_call_guardrails,
)
from ravi.kernel.tools.parsing import ParsedToolCall, find_tool, parse_tool_call
from ravi.kernel.tools.approval import tool_needs_approval
from ravi.reasoning.agents.assistant._tool_execution import (
    ToolExecutionContext,
    build_tool_error,
    execute_tool_direct,
    execute_tool_via_runtime,
    request_tool_approval,
)
from ravi.reasoning.agents.assistant._stream_handler import (
    handle_stream_final_response,
    process_stream_tool_calls,
)
from ravi.exceptions import GuardrailTripwireError
from ravi.kernel.guardrails.base_guardrail import (
    GuardrailResult,
    GuardrailType,
)
from ravi.reasoning.hooks.manager import HookEvent, HookManager
from ravi.kernel.memory.history_provider import HistoryProvider
from ravi.kernel.memory.memory_scope import MemoryScope
from ravi.kernel.messages.base_message import BaseClientMessage
from ravi.kernel.middleware.base import (
    BaseMiddleware,
    MiddlewareContext,
    MiddlewareStage,
)
from ravi.kernel.middleware.runner import MiddlewarePipeline
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
from ravi.fabric.resilience.policies import (
    LLM_RETRY_POLICY,
    RetryPolicy,
    TOOL_RETRY_POLICY,
    _calculate_delay,
)
from ravi.fabric.agents_base.agent_context import AgentContext
from ravi.kernel.tools import BaseTool
from ravi.kernel.guardrails.spec import GuardrailSpec
from ravi.fabric.catalog import AgentCatalogRegistry
from ravi.catalog import SkillManager
from ravi.catalog.tools.capability_search.tool import CapabilitySearchTool
from ravi.fabric.checkpoint import CheckpointStore


def _extract_text(content: list[ContentBlock]) -> str:
    """Extract plain text from a content block list."""
    parts: list[str] = []
    for block in content:
        if hasattr(block, "text"):
            parts.append(block.text)
        elif isinstance(block, str):
            parts.append(block)
    return " ".join(parts)


@register_agent("assistant")
class AssistantAgent(ActorAgent):
    """Full cognitive actor: ReAct loop inside the actor fabric.

    on_message() is the single entry point.  All communication flows through
    the runtime — no standalone run() call.

    For streaming, send a ``StreamEnvelope`` payload; the agent emits
    ``StreamChunk`` objects to ``envelope.channel`` as it processes.

    Usage::

        runtime = LocalRuntime()
        await runtime.start()

        llm = OpenAIClient(model="gpt-4o")
        agent = AssistantAgent(
            "researcher", runtime,
            model=llm,
            tools=[calc_tool],
        )
        await agent.start()

        proxy = UserProxyAgent("proxy", runtime)
        await proxy.start()

        result = await proxy.ask("Find the top 3 repos for user X", recipient=agent.id)
        print(result.output)
    """

    DEFAULT_MAX_ACTIVE_TOOLS = 8
    _DEFAULT_SYSTEM_INSTRUCTIONS = (
        "You are a helpful AI assistant. Use the provided tools to solve "
        "the user's request. Think step-by-step."
    )

    def __init__(
        self,
        name: str,
        runtime: AgentRuntime,
        *,
        # Cognitive resources — explicit, no more catalog lookups
        model: Optional["BaseModelClient"] = None,
        context: Optional[AgentContext] = None,
        checkpoint_store: Optional[CheckpointStore] = None,
        # Tools and skills — via catalog or list
        catalog: Optional[AgentCatalogRegistry] = None,
        tools: Optional[List[BaseTool]] = None,
        # Safety
        guardrails: Optional[GuardrailSpec] = None,
        # Everything else
        description: str = "",
        key: str = "default",
        system_instructions: str = _DEFAULT_SYSTEM_INSTRUCTIONS,
        memory_scope: MemoryScope = MemoryScope.ISOLATED,
        session_id: Optional[str] = None,
        max_iterations: int = 50,
        verbose: bool = True,
        hooks: Optional[HookManager] = None,
        llm_retry_policy: Optional[RetryPolicy] = None,
        tool_retry_policy: Optional[RetryPolicy] = None,
        run_timeout: Optional[float] = None,
        tool_timeout: Optional[float] = 30.0,
        tool_approval_handler: Optional[ToolApprovalHandler] = None,
        tools_requiring_approval: Optional[List[str]] = None,
        response_schema: Optional[type] = None,
        middleware: Optional[List[BaseMiddleware]] = None,
        execution_context: Optional[ExecutionContext] = None,
        enable_capability_search: bool = True,
        checkpoint_every: int = 0,
        subscriptions: Optional[List[TopicId]] = None,
    ):
        # Resolve cognitive resources
        if context is None:
            from ravi.fabric.memory.in_memory import InMemoryHistoryProvider as _IMP
            from ravi.reasoning.memory.context.sliding_window import SlidingWindowStrategy

            context = AgentContext(
                history=_IMP(),
                compaction_strategies=[SlidingWindowStrategy(max_messages=40)]
            )

        # Build slim tool catalog
        resolved_catalog: AgentCatalogRegistry = catalog or AgentCatalogRegistry()
        if tools:
            for t in tools:
                resolved_catalog.register_tool(t)
        if enable_capability_search and resolved_catalog.get_tool("capability_search") is None:
            resolved_catalog.register_tool(CapabilitySearchTool(resolved_catalog))

        # Convert GuardrailSpec to middleware
        resolved_middleware: List[BaseMiddleware] = list(middleware or [])
        if guardrails and not guardrails.is_empty():
            from ravi.reasoning.middleware.guardrails import GuardrailsMiddleware
            resolved_middleware.append(GuardrailsMiddleware(
                input_guardrails=guardrails.input,
                output_guardrails=guardrails.output,
                tool_call_guardrails=guardrails.tool_call,
            ))

        super().__init__(
            name=name,
            runtime=runtime,
            key=key,
            description=description,
            catalog=resolved_catalog,
            subscriptions=subscriptions,
        )

        # Cognitive resources
        self.model_client = model
        self.model_context = context
        self.history: HistoryProvider = context.history
        self._catalog = resolved_catalog
        self.skill_manager: Optional[SkillManager] = resolved_catalog.skill_manager

        # System instructions (read-only externally; mutated via rewrite_system_prompt)
        self._system_instructions: str = system_instructions
        self.memory_scope = memory_scope
        self.response_schema: Optional[type] = response_schema
        self.execution_context: Optional[ExecutionContext] = execution_context
        self.middleware_pipeline = MiddlewarePipeline(resolved_middleware)

        # Config
        self.max_iterations = max_iterations
        self.verbose = verbose
        self.hooks = hooks or HookManager()
        self.llm_retry_policy = llm_retry_policy or LLM_RETRY_POLICY
        self.tool_retry_policy = tool_retry_policy or TOOL_RETRY_POLICY
        self.run_timeout = run_timeout
        self.tool_timeout = tool_timeout

        # HITL
        self.tool_approval_handler = tool_approval_handler
        self.tools_requiring_approval = tools_requiring_approval
        self._active_tool_names: set[str] = set()
        self._tool_search_name = "capability_search"
        self._always_visible_tool_names = {
            n
            for n in (self._tool_search_name, "ask_human")
            if self._catalog.get_tool(n) is not None
        }
        self._max_active_tools = self.DEFAULT_MAX_ACTIVE_TOOLS

        # Fault recovery
        self.checkpoint_store: Optional[CheckpointStore] = checkpoint_store
        self.checkpoint_every: int = checkpoint_every

        # Per-run state
        self._current_run_id: str = ""
        self._session_id: str = session_id or name

    # -- System instructions -------------------------------------------------

    @property
    def system_instructions(self) -> str:
        return self._system_instructions

    @system_instructions.setter
    def system_instructions(self, value: str) -> None:
        raise AttributeError(
            "system_instructions is read-only. Call rewrite_system_prompt()."
        )

    def _update_system_instructions(self, value: str) -> None:
        self._system_instructions = value

    def get_system_instructions(self) -> str:
        return self._system_instructions

    def get_effective_system_prompt(self) -> str:
        base = self.get_system_instructions()
        if hasattr(self._catalog, "inject_into_prompt"):
            return self._catalog.inject_into_prompt(base)
        return base

    # -- Actor entry point ---------------------------------------------------

    async def on_message(
        self, ctx: MessageContext, content: list[ContentBlock]
    ) -> object:
        """Runtime entry point — dispatches to streaming or non-streaming run."""
        payload = content[0] if content else None
        if isinstance(payload, StreamEnvelope):
            # Spawn streaming as a background task so on_message returns immediately.
            # The _agent_loop is unblocked; _run_stream_impl runs independently and
            # emits chunks to the channel until close() is called.
            task = asyncio.create_task(
                self._run_stream_impl(payload.task, channel=payload.channel)
            )
            task.add_done_callback(
                lambda t: (
                    logger.error("stream task failed: %s", t.exception())
                    if not t.cancelled() and t.exception() is not None
                    else None
                )
            )
            return None
        text = _extract_text(content) if content else ""
        return await self._run_impl(text)

    # -- Dynamic reconfiguration --------------------------------------------

    async def add_tool(self, tool: object) -> bool:
        """Dynamically register a tool on this agent's catalog."""
        self._catalog.register_tool(tool)  # type: ignore[arg-type]
        return True

    async def rewrite_system_prompt(self, new_instructions: str) -> bool:
        """Rewrite this agent's system instructions."""
        self._update_system_instructions(new_instructions)
        return True

    # -- Checkpoint helpers --------------------------------------------------

    async def reset(self) -> None:
        """Clear memory and return agent to initial state."""
        await self.history.clear_session(self._session_id)
        self._reset_tool_activation_state()
        self._reset_hitl_tools()

    def _reset_hitl_tools(self) -> None:
        from ravi.kernel.tools.base_tool import ResettableTool

        for tool in self.tools:
            if isinstance(tool, ResettableTool):
                tool.reset()

    def _reset_tool_activation_state(self) -> None:
        self._active_tool_names.clear()

    async def _save_checkpoint(
        self, run_id: str, iteration: int, steps: list[StepResult]
    ) -> None:
        if self.checkpoint_store is None:
            return
        from ravi.fabric.checkpoint import RunCheckpoint

        agent_id = (
            self.execution_context.agent_id
            if self.execution_context and self.execution_context.agent_id
            else self.name
        )
        thread_id = self.execution_context.thread_id if self.execution_context else ""
        messages = await self.history.load_messages(self._session_id)
        root = await self.checkpoint_store.load(run_id, agent_id)
        if root is None:
            root = RunCheckpoint(run_id=run_id, agent_id=agent_id, thread_id=thread_id)
        root.mark_in_progress(iteration=iteration)
        root.messages = [m.model_dump(mode="json") for m in messages]
        if hasattr(self.runtime, "resource_locks"):
            root.resource_locks = self.runtime.resource_locks.snapshot()
        await self.checkpoint_store.save(root)

    async def run_with_recovery(
        self, input_text: str, *, response_schema: Optional[type] = None, **kwargs
    ) -> AgentRunResult:
        """Run with automatic checkpoint recovery."""
        if self.checkpoint_store is not None:
            run_id = self._resolve_run_id()
            agent_id = (
                self.execution_context.agent_id
                if self.execution_context and self.execution_context.agent_id
                else self.name
            )
            checkpoint = await self.checkpoint_store.load(run_id, agent_id)
            if checkpoint is not None and checkpoint.messages:
                from ravi.kernel.messages.client_messages import (
                    AssistantMessage,
                    SystemMessage,
                    ToolCallMessage,
                    ToolExecutionResultMessage,
                    UserMessage,
                )

                await self.history.clear_session(self._session_id)
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
                            await self._append(cls.model_validate(msg_dict))
                        except Exception:
                            pass
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
        return await self._run_impl(
            input_text, response_schema=response_schema, **kwargs
        )

    def _resolve_run_id(self) -> str:
        if self.execution_context is not None and self.execution_context.run_id:
            return self.execution_context.run_id
        return str(uuid4())

    async def _append(self, *messages: BaseClientMessage) -> None:
        """Append one or more messages to this agent's session history."""
        if messages:
            await self.history.save_messages(self._session_id, list(messages))

    async def _record_lineage(
        self,
        msg: BaseClientMessage,
        parent_message_id: Optional[str] = None,
        tool_call_id: Optional[str] = None,
    ) -> None:
        try:
            lineage = self._catalog.resolve("lineage")
        except Exception:
            lineage = None
        if lineage is None or not hasattr(lineage, "record"):
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
        await lineage.record(session_id, msg.id, prov)

    @staticmethod
    def _resolve_requested_tool_choice(
        tool_schemas: List[JsonObject],
        requested_tool_choice: Optional[str | JsonObject] = None,
    ) -> Optional[str | JsonObject]:
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

    # -- Server compatibility shims ------------------------------------------
    # These allow the existing server layer (stream_agent_run, chat.py) to work
    # without a full rewrite.  They delegate to the internal implementations.
    # New code should go through on_message() / UserProxyAgent.

    async def run(
        self,
        input_text: str,
        *,
        response_schema: Optional[type] = None,
        **kwargs,
    ) -> AgentRunResult:
        """Server-compat shim — delegates to _run_impl()."""
        return await self._run_impl(
            input_text, response_schema=response_schema, **kwargs
        )

    async def run_stream(
        self,
        input_text: str,
        *,
        response_schema: Optional[type] = None,
        **kwargs,
    ) -> AsyncIterator[Union[StreamChunk, dict, ToolExecutionResultMessage]]:
        """Server-compat shim — delegates to _run_stream_inner()."""
        async for chunk in self._run_stream_inner(
            input_text, response_schema=response_schema, **kwargs
        ):
            yield chunk

    # -- Non-streaming implementation ----------------------------------------

    async def _run_impl(
        self,
        input_text: str,
        *,
        response_schema: Optional[type] = None,
        **kwargs,
    ) -> AgentRunResult:
        """Execute agent to completion (non-streaming)."""
        self._reset_tool_activation_state()
        _schema = (
            response_schema if response_schema is not None else self.response_schema
        )
        if self.run_timeout:
            return await asyncio.wait_for(
                self._run_inner(input_text, response_schema=_schema, **kwargs),
                timeout=self.run_timeout,
            )
        return await self._run_inner(input_text, response_schema=_schema, **kwargs)

    async def run_structured(
        self,
        input_text: str,
        schema: "type",
        *,
        max_iterations: Optional[int] = None,
        **kwargs,
    ) -> "StructuredOutputResult":
        """Run and emit a typed structured answer."""
        _saved_max = self.max_iterations
        if max_iterations is not None:
            self.max_iterations = max_iterations
        try:
            result = await self._run_impl(input_text, response_schema=schema, **kwargs)
        finally:
            self.max_iterations = _saved_max
        if result.structured_output is None:
            from ravi.kernel.structured import StructuredOutputError

            raise StructuredOutputError(
                f"Structured extraction did not produce a result (status={result.status.value})"
            )
        return result.structured_output

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
        final_output: List[MediaType] = []
        guardrail_results: List[GuardrailResult] = []
        response: Optional[AssistantMessage] = None
        _run_end_dispatched = False
        initial_tool_choice = kwargs.pop("tool_choice", None)

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
            input_text, user_message_content
        )

        try:
            with global_tracer.start_span("agent_run", attrs) as run_span:
                global_metrics.increment_counter("agent_runs", tags={"name": self.name})
                if self.verbose:
                    logger.info(f"[{self.name}] Starting run: {input_text[:80]}...")

                await self.hooks.dispatch(
                    HookEvent.RUN_START,
                    {
                        "event": "on_run_start",
                        "agent_name": self.name,
                        "run_id": run_id,
                        "input_text": input_text,
                    },
                )

                await self._append(persisted_user_message)
                await self._record_lineage(persisted_user_message)
                last_msg_id = persisted_user_message.id

                for step_num in range(1, self.max_iterations + 1):
                    with global_tracer.start_span(
                        f"step_{step_num}", {"step": step_num}
                    ):
                        if (
                            self.execution_context is not None
                            and not self.execution_context.is_alive
                        ):
                            status = RunStatus.CANCELLED
                            error_msg = (
                                "Execution context cancelled or deadline exceeded"
                            )
                            break

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
                        await self._append(response)
                        await self._record_lineage(
                            response, parent_message_id=last_msg_id
                        )
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
                                await self._append(tool_msg)
                                await self._record_lineage(
                                    tool_msg,
                                    parent_message_id=last_msg_id,
                                    tool_call_id=parsed.id,
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
                            await self._append(tool_msg)
                            await self._record_lineage(
                                tool_msg,
                                parent_message_id=last_msg_id,
                                tool_call_id=parsed.call_id,
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

                        if (
                            self.checkpoint_store is not None
                            and self.checkpoint_every > 0
                            and step_num % self.checkpoint_every == 0
                        ):
                            await self._save_checkpoint(run_id, step_num, steps)

                else:
                    status = RunStatus.MAX_ITERATIONS
                    if self.verbose:
                        logger.warning(
                            f"[{self.name}] Hit max iterations ({self.max_iterations})"
                        )
                    if steps and steps[-1].thought:
                        final_output = steps[-1].thought

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

                if response_schema is not None and status == RunStatus.COMPLETED:
                    _last_msg = steps[-1] if steps else None
                    _parsed = None
                    if _last_msg is not None and response is not None:
                        _parsed = getattr(response, "parsed", None)
                    if _parsed is not None:
                        from ravi.kernel.structured.result import StructuredOutputResult

                        raw_text = extract_text(response) or ""
                        result.structured_output = StructuredOutputResult(
                            parsed=_parsed,
                            raw_text=raw_text,
                            model=getattr(self.model_client, "model", None),
                        )
                    else:
                        memory_messages = await self.history.load_messages(self._session_id)
                        context_messages = await self.model_context.build(
                            session_id=self._session_id,
                            current_input=input_text,
                            raw_messages=prepare_model_context_messages(
                                memory_messages,
                                input_text,
                                user_message_content,
                            ),
                            model_client=self.model_client,
                        )
                        clean_ctx = [
                            m
                            for m in context_messages
                            if not isinstance(m, SystemMessage)
                        ]
                        result.structured_output = await self.model_client.generate(
                            clean_ctx,
                            system_instructions=self.get_effective_system_prompt(),
                            response_format=response_schema,
                        )

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
                    pass
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

    # -- Streaming implementation --------------------------------------------

    async def _run_stream_impl(self, task: str, *, channel: StreamChannel) -> None:
        """Run in streaming mode, emitting chunks to ``channel``."""
        try:
            async for chunk in self._run_stream_inner(task):
                await channel.emit(chunk)
                await asyncio.sleep(0)  # yield between chunks so consumers run
        finally:
            channel.close()

    async def _run_stream_inner(
        self,
        input_text: str,
        *,
        response_schema: Optional[type] = None,
        **kwargs,
    ) -> AsyncIterator[Union[StreamChunk, dict, ToolExecutionResultMessage]]:
        """Internal streaming generator (mirrors ReActAgent.run_stream internals)."""
        self._reset_tool_activation_state()

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
            input_text, user_message_content
        )
        initial_tool_choice = kwargs.pop("tool_choice", None)

        _stream_pub = None
        if hasattr(self.runtime, "publish_message"):
            _stream_pub = StreamPublisher(
                self.runtime,
                TopicId(type="stream", source=self.id.key),
                sender=self.id,
            )

        try:
            with global_tracer.start_span("agent_run_stream", attrs):
                global_metrics.increment_counter("agent_runs", tags={"name": self.name})
                if self.verbose:
                    logger.info(
                        f"[{self.name}] Starting streaming run: {input_text[:80]}..."
                    )

                await self._append(persisted_user_message)

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
                        tool_schemas = self._build_tool_schemas(
                            current_input=input_text
                        )
                        memory_messages = await self.history.load_messages(self._session_id)
                        messages = await self.model_context.build(
                            session_id=self._session_id,
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
                                    final_response_obj = normalize_textual_tool_calls(
                                        final_response_obj
                                    )
                                    await self._append(final_response_obj)

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
                                    await self._append(final_response_obj)
                                elif partial_text:
                                    await self._append(
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
                            role="assistant", content=None
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
                                history=self.history,
                                session_id=self._session_id,
                                input_text=input_text,
                                response_schema=_schema,
                                stream_pub=_stream_pub,
                                extract_text_fn=extract_text,
                            ):
                                yield chunk
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
                            "execute_tools_stream", {"count": len(response.tool_calls)}
                        ):
                            async for tool_msg in process_stream_tool_calls(
                                response=response,
                                input_guardrails=input_guardrails,
                                output_guardrails=output_guardrails,
                                agent_name=self.name,
                                run_id=run_id,
                                step_num=step_num,
                                history=self.history,
                                session_id=self._session_id,
                                execute_tool_fn=self._execute_tool,
                                stream_pub=_stream_pub,
                                tool_timeout=self.tool_timeout,
                            ):
                                yield tool_msg
        finally:
            self._current_run_id = ""

    # -- Tool schema helpers -------------------------------------------------

    def _tool_needs_approval(self, tool_name: str) -> bool:
        return tool_needs_approval(tool_name, self.tools_requiring_approval)

    def _bootstrap_active_tools(self, current_input: str) -> None:
        actual_tools = [
            t for t in self.tools if getattr(t, "name", None) != self._tool_search_name
        ]
        if len(actual_tools) <= self._max_active_tools:
            self._active_tool_names.update(t.name for t in actual_tools)
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
        for tool_name in tool_names:
            if self._catalog.get_tool(tool_name) is not None:
                self._active_tool_names.add(tool_name)

    def _build_tool_schemas(self, current_input: str = "") -> List[JsonObject]:
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

    # -- LLM call ------------------------------------------------------------

    async def _call_llm(
        self,
        current_input: str = "",
        *,
        response_schema: Optional[type] = None,
        input_content: Optional[list[MediaType]] = None,
        **kwargs,
    ) -> AssistantMessage:
        tool_schemas = self._build_tool_schemas(current_input=current_input)
        requested_tool_choice = kwargs.pop("tool_choice", None)
        user_message_content = resolve_user_message_content(
            current_input, input_content
        )
        memory_messages = await self.history.load_messages(self._session_id)
        messages = await self.model_context.build(
            session_id=self._session_id,
            current_input=current_input,
            raw_messages=prepare_model_context_messages(
                memory_messages,
                current_input,
                user_message_content,
            ),
            model_client=self.model_client,
        )

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
                            "AssistantAgent expected AssistantMessage from generate()"
                        )

                    response = normalize_textual_tool_calls(response)
                    llm_t1 = asyncio.get_event_loop().time()
                    global_metrics.record_histogram(
                        "llm_latency",
                        llm_t1 - llm_t0,
                        tags={"model": getattr(self.model_client, "model", "unknown")},
                    )

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
                            f"{self.llm_retry_policy.max_retries}: {e} (waiting {delay:.1f}s)"
                        )
                        await asyncio.sleep(delay)
                    else:
                        global_metrics.increment_counter(
                            "llm_errors", tags={"error": type(e).__name__}
                        )
                        raise
                except Exception as e:
                    global_metrics.increment_counter(
                        "llm_errors", tags={"error": type(e).__name__}
                    )
                    raise

        if last_exception:
            raise last_exception
        raise RuntimeError("LLM call failed unexpectedly")

    # -- Tool execution ------------------------------------------------------

    async def _execute_tool(
        self,
        parsed: ParsedToolCall,
        step_num: int,
    ) -> Tuple[ToolCallRecord, ToolExecutionResultMessage]:
        with global_tracer.start_span("tool_execution", {"tool": parsed.name}) as span:
            t0 = time.monotonic()
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

            tool = find_tool(parsed.name, self.tools, catalog=self._catalog)
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
                agent_id=self.id,
            )

            if (
                self.runtime is not None
                and tool is not None
                and getattr(tool, "agent_id", None) is not None
            ):
                return await execute_tool_via_runtime(
                    parsed, step_num, t0, span, exec_ctx
                )

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
                    f"Tool '{parsed.name}' is a raw dict schema, not executable.",
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

            return await execute_tool_direct(parsed, step_num, t0, span, tool, exec_ctx)
