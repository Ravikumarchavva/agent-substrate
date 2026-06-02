"""ReActAgent — ReAct loop built on the kernel/fabric model.

The loop:
  build list[ChatMessage]
  → LLMClient.generate() → list[ContentBlock]
  → extract ToolUseBlock → Tool.execute() → ToolResultBlock
  → repeat until no tool calls or max_iterations

Entry points:
  run(input_text)          → AgentRunResult   (non-streaming)
  run_stream(input_text)   → AsyncIterator[TextDelta | ReasoningDelta |
                                           CompletionEvent | StreamDone]
  on_message(ctx, payload) → actor entry point (delegates to run)

Two ids govern scope:
  session_id — the conversation thread (long-lived; many runs).
               History is always keyed by session_id.
               Precedence: explicit arg → supervision.session_id → self.id.key
  run_id     — this execution tree (short-lived; one run() call).
               Scopes budget, supervision, resume, and the progress topic.
               Precedence: explicit arg → supervision.run_id → fresh uuid

Resumability:
  run(input_text, resume=True, run_id=<existing_run_id>, session_id=<thread_id>)
  — reload history for (agent_id, session_id) and continue from the last
  committed step. The persisted history IS the checkpoint. The loop position
  is inferred from the tail of the history.

Priority / pausing:
  If self.spawn_budget is set and is_paused(self.id) is True before an LLM
  call, the agent stops cleanly and returns status="paused". The orchestrator
  can resume it later via run(..., resume=True).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable
from uuid import uuid4

from ravi.kernel import (
    AgentId,
    AgentRuntime,
    ChatMessage,
    ContentBlock,
    ErrorBlock,
    MessageContext,
    Supervision,
    TextBlock,
    Tool,
    ToolResultBlock,
    ToolRisk,
    ToolUseBlock,
    AgentCrashError,
)
from ravi.kernel.message import Message
from ravi.kernel.stream import AgentProgress, AgentStep, CompletionEvent, ReasoningDelta, StreamDone, TextDelta
from ravi.agents.context import AgentContext, HistoryProvider
from ravi.agents.context.context import DefaultAgentContext
from ravi.kernel import Skill
from ravi.kernel.llm import LLMClient
from ravi.agents.hooks.manager import HookEvent, HookManager
from ravi.agents.resources.budget import BudgetExceededError, ExecutionBudget
from ravi.agents.guardrails.runner import (
    check_input_guardrails,
    check_output_guardrails,
    check_tool_call_guardrails,
)
from ravi.exceptions import GuardrailTripwireError

logger = logging.getLogger(__name__)

# Signature: (tool_name, arguments) → True=approved, False=denied
ApprovalHandler = Callable[[str, dict[str, Any]], Awaitable[bool]]


# ---------------------------------------------------------------------------
# Local result types
# ---------------------------------------------------------------------------


@dataclass
class ToolCallRecord:
    name: str
    call_id: str
    arguments: dict[str, Any]
    result: str
    is_error: bool
    duration_ms: float


@dataclass
class _Finished:
    result: 'AgentRunResult'


@dataclass
class AgentRunResult:
    """Result of a completed agent run."""

    output: str
    status: str  # "success" | "error" | "max_iterations" | "guardrail_tripped" | "paused"
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    run_id: str = ""
    error: str | None = None

    def model_dump(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "output": self.output,
            "status": self.status,
            "run_id": self.run_id,
            "error": self.error,
            "tool_calls": [
                {
                    "name": r.name,
                    "call_id": r.call_id,
                    "result": r.result,
                    "is_error": r.is_error,
                    "duration_ms": r.duration_ms,
                }
                for r in self.tool_calls
            ],
        }


# ---------------------------------------------------------------------------
# ReActAgent
# ---------------------------------------------------------------------------


class ReActAgent:
    """Full ReAct agent on the kernel/fabric model.

    Usage::

        from ravi.adapters.llm.openai import OpenAIClient
        from ravi.agents.runtime import LocalRuntime
        from ravi.agents.context import AgentContext, SlidingWindowCompaction
        from ravi.adapters.memory import RedisHistoryProvider
        from ravi.kernel import Skill

        runtime = LocalRuntime()
        await runtime.start()

        agent = ReActAgent(
            "researcher", runtime,
            model=OpenAIClient(model="gpt-4o"),
            tools=[calc_tool],
            skills=[Skill(name="math", instructions="Show your working step by step.")],
            system_instructions="You are a helpful AI assistant.",
            context=AgentContext(
                RedisHistoryProvider(session_id="sess-123"),
                [SlidingWindowCompaction(max_messages=60)],
            ),
        )
        result = await agent.run("What is 42 * 17?")
        print(result.output)
    """

    _DEFAULT_SYSTEM = (
        "You are a helpful AI assistant. "
        "Use the provided tools to solve the user's request step by step."
    )

    def __init__(
        self,
        name: str,
        runtime: AgentRuntime,
        *,
        model: LLMClient,
        tools: list[Tool] | None = None,
        skills: list[Skill] | None = None,
        system_instructions: str | None = None,
        max_iterations: int = 20,
        tool_timeout: float | None = 30.0,
        context: AgentContext | None = None,
        guardrails: list[object] | None = None,
        approval_handler: ApprovalHandler | None = None,
        approval_required_risk: ToolRisk = ToolRisk.HIGH,
        hooks: HookManager | None = None,
        supervision: Supervision | None = None,
        execution_budget: ExecutionBudget | None = None,
    ) -> None:
        self.name = name
        self.runtime = runtime
        self.id = AgentId(type="assistant", key=name)
        self.supervision: Supervision | None = supervision
        self.model = model
        self._tools: dict[str, Tool] = {t.name: t for t in (tools or [])}
        self._skills: list[Skill] = list(skills or [])
        self._base_system = system_instructions or self._DEFAULT_SYSTEM
        self.max_iterations = max_iterations
        self.tool_timeout = tool_timeout
        self._guardrails: list[object] = list(guardrails or [])
        self._approval_handler = approval_handler
        self._approval_required_risk = approval_required_risk
        self.hooks = hooks or HookManager()
        self.execution_budget: ExecutionBudget | None = execution_budget

        # Set by the orchestrator when this agent is a subagent in a supervised tree.
        # Checked before each LLM call; if True, agent pauses cooperatively.
        self.spawn_budget: Any | None = None  # SpawnBudget | None

        _ctx = context if context is not None else AgentContext.default()
        self._ctx = DefaultAgentContext(self.id, _ctx.history, _ctx.compaction)

    @property
    def _system(self) -> str:
        """Effective system prompt: base instructions + all skills' instructions."""
        if not self._skills:
            return self._base_system
        skill_blocks = "\n\n".join(
            f"## Skill: {s.name}\n{s.instructions}" for s in self._skills
        )
        return f"{self._base_system}\n\n{skill_blocks}"

    @property
    def history(self) -> HistoryProvider:
        return self._ctx.history

    # -- Tool registry -------------------------------------------------------

    def register_tool(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get_tool(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[Tool]:
        return list(self._tools.values())

    def _tool_schemas(self) -> list[dict[str, object]] | None:
        """Convert registered tools to the dict format expected by LLMClient."""
        if not self._tools:
            return None
        return [
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            }
            for t in self._tools.values()
        ]

    # -- Actor entry point ---------------------------------------------------

    async def on_message(self, _ctx: MessageContext, payload: object) -> object:
        """Actor runtime entry point — delegates to run()."""
        if isinstance(payload, str):
            return await self.run(payload)
        if isinstance(payload, list):
            text = " ".join(b.text for b in payload if isinstance(b, TextBlock))
            return await self.run(text)
        return None

    # -- Non-streaming run ---------------------------------------------------

    async def _react(
        self,
        input_text: str,
        *,
        session_id: str | None = None,
        resume: bool = False,
        run_id: str | None = None,
        stream: bool = False,
    ) -> AsyncIterator[TextDelta | ReasoningDelta | CompletionEvent | _Finished]:
        # -- History scope (the conversation thread) ---------------------------
        if session_id is not None:
            sid = session_id
        elif self.supervision is not None:
            sid = self.supervision.session_id
        else:
            sid = self.id.key   # stable standalone thread

        # -- Execution scope (this turn) ---------------------------------------
        if run_id is not None:
            rid = run_id
        elif self.supervision is not None:
            rid = self.supervision.run_id
        else:
            rid = uuid4().hex

        tool_calls: list[ToolCallRecord] = []

        await self.hooks.dispatch(
            HookEvent.RUN_START,
            {"agent": self.name, "run_id": rid, "session_id": sid, "input": input_text[:80]},
        )
        await self._emit_progress(AgentStep.STARTED, input_text[:120], rid)
        logger.info("[%s] run start (resume=%s, stream=%s, session=%s): %.80s", self.name, resume, stream, sid, input_text)

        if not resume:
            await self._append(
                ChatMessage(role="user", content=[TextBlock(text=input_text)]),
                sid,
                rid,
            )

        try:
            # -- input guardrails ------------------------------------------------
            if self._guardrails and not resume:
                await check_input_guardrails(
                    guardrails=self._guardrails,
                    agent_name=self.name,
                    run_id=rid,
                    input_text=input_text,
                )

            for step in range(1, self.max_iterations + 1):
                # -- cooperative pause check ------------------------------------
                if self.spawn_budget is not None and self.spawn_budget.is_paused(self.id):
                    logger.info("[%s] paused by priority preemption at step %d", self.name, step)
                    await self._emit_progress(AgentStep.PAUSED, "paused by priority preemption", rid)
                    await self.hooks.dispatch(
                        HookEvent.RUN_END,
                        {"agent": self.name, "run_id": rid, "status": "paused"},
                    )
                    last_output = await self._last_assistant_text(sid)
                    yield _Finished(AgentRunResult(
                        output=last_output,
                        status="paused",
                        tool_calls=tool_calls,
                        run_id=rid,
                    ))
                    return

                await self.hooks.dispatch(
                    HookEvent.STEP_START, {"agent": self.name, "step": step}
                )
                messages = await self._prompt_window(sid)

                await self.hooks.dispatch(
                    HookEvent.LLM_START,
                    {"agent": self.name, "step": step, "message_count": len(messages)},
                )
                await self._emit_progress(
                    AgentStep.THINKING, f"step {step}", rid
                )

                # -- ExecutionBudget: count prompt tokens for cost estimation --
                if self.execution_budget is not None:
                    try:
                        prompt_tokens = await self.model.count_tokens(messages)
                    except Exception:
                        prompt_tokens = 0

                content: list[ContentBlock] = []
                if stream:
                    async for event in _stream_generate(
                        self.model, messages, self._system, self._tool_schemas()
                    ):
                        if isinstance(event, (TextDelta, ReasoningDelta)):
                            yield event
                        elif isinstance(event, CompletionEvent):
                            content = event.content
                else:
                    content = await self.model.generate(
                        messages, tools=self._tool_schemas(), system=self._system
                    )

                # -- ExecutionBudget: record this turn's usage -----------------
                if self.execution_budget is not None:
                    try:
                        out_tokens = await self.model.count_tokens(
                            [ChatMessage(role="assistant", content=content)]
                        )
                    except Exception:
                        out_tokens = 0
                    self.execution_budget.consume(
                        tokens=prompt_tokens + out_tokens, turns=1
                    )

                await self.hooks.dispatch(
                    HookEvent.LLM_END, {"agent": self.name, "step": step}
                )

                if not content:
                    break

                tool_uses = [b for b in content if isinstance(b, ToolUseBlock)]
                await self._append(ChatMessage(role="assistant", content=content), sid, rid)

                if not tool_uses:
                    output = _content_to_str(content)

                    # -- output guardrails ---------------------------------------
                    if self._guardrails:
                        await check_output_guardrails(
                            guardrails=self._guardrails,
                            agent_name=self.name,
                            run_id=rid,
                            output_text=output,
                        )

                    logger.info("[%s] done at step %d", self.name, step)
                    await self.hooks.dispatch(
                        HookEvent.STEP_END,
                        {"agent": self.name, "step": step, "has_tool_calls": False},
                    )
                    await self.hooks.dispatch(
                        HookEvent.RUN_END,
                        {"agent": self.name, "run_id": rid, "status": "success"},
                    )
                    await self._emit_progress(
                        AgentStep.DONE, output[:120], rid
                    )
                    if stream:
                        yield CompletionEvent(content=content)
                    
                    yield _Finished(AgentRunResult(
                        output=output,
                        status="success",
                        tool_calls=tool_calls,
                        run_id=rid,
                    ))
                    return

                logger.info(
                    "[%s] step %d: tools → %s",
                    self.name,
                    step,
                    [b.tool_name for b in tool_uses],
                )
                result_blocks: list[ContentBlock] = []
                for tu in tool_uses:
                    call_progress = await self._emit_progress(
                        AgentStep.TOOL_CALL,
                        tu.tool_name,
                        rid,
                        call_id=tu.call_id,
                    )
                    if stream and call_progress is not None:
                        yield call_progress
                    # -- tool-call guardrails ------------------------------------
                    if self._guardrails:
                        await check_tool_call_guardrails(
                            guardrails=self._guardrails,
                            agent_name=self.name,
                            run_id=rid,
                            tool_use=tu,
                        )
                    record, block = await self._execute_tool(tu)
                    result_progress = await self._emit_progress(
                        AgentStep.TOOL_RESULT,
                        f"{tu.tool_name}: {'error' if record.is_error else 'ok'}",
                        rid,
                        call_id=tu.call_id,
                    )
                    if stream and result_progress is not None:
                        yield result_progress
                    tool_calls.append(record)
                    result_blocks.append(block)

                await self._append(ChatMessage(role="tool", content=result_blocks), sid, rid)
                await self.hooks.dispatch(
                    HookEvent.STEP_END,
                    {"agent": self.name, "step": step, "has_tool_calls": True},
                )

            logger.warning(
                "[%s] hit max_iterations (%d)", self.name, self.max_iterations
            )
            last_output = await self._last_assistant_text(sid)
            await self.hooks.dispatch(
                HookEvent.RUN_END,
                {"agent": self.name, "run_id": rid, "status": "max_iterations"},
            )
            await self._emit_progress(
                AgentStep.DONE, last_output[:120], rid, status="max_iterations"
            )
            yield _Finished(AgentRunResult(
                output=last_output,
                status="max_iterations",
                tool_calls=tool_calls,
                run_id=rid,
            ))
            return

        except GuardrailTripwireError as exc:
            logger.warning("[%s] guardrail tripped: %s", self.name, exc.message)
            await self.hooks.dispatch(
                HookEvent.RUN_END,
                {"agent": self.name, "run_id": rid, "status": "guardrail_tripped"},
            )
            await self._emit_progress(
                AgentStep.ERROR, f"guardrail: {exc.message}", rid
            )
            yield _Finished(AgentRunResult(
                output=f"Request blocked: {exc.message}",
                status="guardrail_tripped",
                tool_calls=tool_calls,
                run_id=rid,
                error=exc.message,
            ))
            return

        except BudgetExceededError as exc:
            logger.warning("[%s] budget exceeded: %s", self.name, exc)
            await self._emit_progress(AgentStep.ERROR, f"budget: {exc}", rid)
            await self.hooks.dispatch(
                HookEvent.RUN_END,
                {"agent": self.name, "run_id": rid, "status": "budget_exceeded"},
            )
            yield _Finished(AgentRunResult(
                output="",
                status="error",
                tool_calls=tool_calls,
                run_id=rid,
                error=str(exc),
            ))
            return

        except Exception as exc:
            logger.exception("[%s] run crashed: %s", self.name, exc)
            await self.hooks.dispatch(
                HookEvent.RUN_END,
                {
                    "agent": self.name,
                    "run_id": rid,
                    "status": "error",
                    "error": str(exc),
                },
            )
            await self._emit_progress(
                AgentStep.ERROR, f"crash: {type(exc).__name__}: {exc}", rid
            )
            raise AgentCrashError(
                f"Agent '{self.name}' crashed: {exc}",
                run_id=rid,
                agent_id=self.id,
            ) from exc

    async def run(
        self,
        input_text: str,
        *,
        session_id: str | None = None,
        resume: bool = False,
        run_id: str | None = None,
    ) -> AgentRunResult:
        """Execute the ReAct loop to completion."""
        async for event in self._react(
            input_text,
            session_id=session_id,
            resume=resume,
            run_id=run_id,
            stream=False,
        ):
            if isinstance(event, _Finished):
                return event.result
        return AgentRunResult(output="", status="error", error="Stream ended without result")

    async def run_stream(
        self,
        input_text: str,
        *,
        session_id: str | None = None,
        resume: bool = False,
        run_id: str | None = None,
    ) -> AsyncIterator[TextDelta | ReasoningDelta | CompletionEvent | StreamDone]:
        """Execute the ReAct loop, yielding progress events."""
        try:
            async for event in self._react(
                input_text,
                session_id=session_id,
                resume=resume,
                run_id=run_id,
                stream=True,
            ):
                if isinstance(event, _Finished):
                    yield StreamDone(
                        reason="success" if event.result.status == "success" else event.result.status
                    )
                    return
                yield event
        except AgentCrashError:
            yield StreamDone(reason="error")
            raise  # propagate crash up
        except Exception:
            yield StreamDone(reason="error")
            raise

    # -- Progress reporting --------------------------------------------------

    async def _emit_progress(

        self, step: str, content: str, run_id: str, **meta: str
    ) -> AgentProgress | None:
        """Publish an ``AgentProgress`` event to the run's shared progress topic.

        All agents in one execution publish to ``TopicId("agent.progress", run_id)``.
        The UI subscribes to that single topic and reconstructs the tree from
        ``agent_id``, ``parent_id``, and ``depth``.

        Root agents (no supervision) fall back to their own id key as the topic
        source so progress is still observable even for standalone agents.

        Returns the event so callers can yield it into a stream if needed.
        Failures are silently swallowed — progress reporting must never crash
        the agent itself.
        """
        from ravi.kernel.identity import TopicId
        sv = self.supervision
        topic_source = run_id if run_id else self.id.key
        event = AgentProgress(
            agent_id=self.id,
            step=step,
            content=content,
            run_id=run_id,
            parent_id=sv.parent_id if sv is not None else None,
            depth=sv.depth if sv is not None else 0,
            metadata=dict(meta),
        )
        try:
            await self.runtime.publish_message(
                event,
                sender=self.id,
                topic=TopicId("agent.progress", topic_source),
            )
        except Exception:
            pass  # progress reporting must not crash the agent
        return event

    # -- Internal helpers ----------------------------------------------------

    async def _append(self, chat_msg: ChatMessage, session_id: str, run_id: str) -> None:
        """Wrap a ChatMessage in a Message envelope and append to history.

        History is keyed by ``session_id`` (the conversation thread).
        ``run_id`` is tagged into the envelope metadata for audit.
        """
        envelope = Message(
            target=self.id,
            payload=chat_msg,
            sender=self.id,
            metadata={"run_id": run_id},
        )
        await self._ctx.history.append(self.id, envelope, session_id=session_id)

    async def _prompt_window(self, session_id: str) -> list[ChatMessage]:
        """Return the compacted history as ChatMessages for the LLM."""
        window = await self._ctx.get_prompt_window(session_id)
        return [m.payload for m in window if isinstance(m.payload, ChatMessage)]

    async def _last_assistant_text(self, session_id: str) -> str:
        window = await self._ctx.get_prompt_window(session_id)
        for msg in reversed(window):
            if isinstance(msg.payload, ChatMessage) and msg.payload.role == "assistant":
                return _content_to_str(msg.payload.content)
        return ""

    async def _execute_tool(
        self, tu: ToolUseBlock
    ) -> tuple[ToolCallRecord, ToolResultBlock]:
        """Run a single tool call, return a record and a ToolResultBlock."""
        t0 = time.monotonic()
        tool = self._tools.get(tu.tool_name)

        if tool is None:
            duration = (time.monotonic() - t0) * 1000
            err = f"Tool '{tu.tool_name}' not found"
            return (
                ToolCallRecord(
                    name=tu.tool_name,
                    call_id=tu.call_id,
                    arguments=dict(tu.arguments),
                    result=err,
                    is_error=True,
                    duration_ms=duration,
                ),
                ToolResultBlock(
                    call_id=tu.call_id,
                    content=[ErrorBlock(error_type="ToolNotFound", message=err)],
                    is_error=True,
                ),
            )

        # -- HITL approval ------------------------------------------------------
        tool_risk = ToolRisk(getattr(tool, "risk", ToolRisk.SAFE))
        _risk_order = {ToolRisk.SAFE: 0, ToolRisk.HIGH: 1, ToolRisk.CRITICAL: 2}
        needs_approval = (
            self._approval_handler is not None
            and _risk_order[tool_risk] >= _risk_order[self._approval_required_risk]
        )
        if needs_approval:
            approved = await self._approval_handler(tu.tool_name, dict(tu.arguments))  # type: ignore[misc]
            if not approved:
                duration = (time.monotonic() - t0) * 1000
                err = f"Tool '{tu.tool_name}' denied by approval handler"
                logger.info("[%s] HITL denied: %s", self.name, tu.tool_name)
                return (
                    ToolCallRecord(
                        name=tu.tool_name,
                        call_id=tu.call_id,
                        arguments=dict(tu.arguments),
                        result=err,
                        is_error=True,
                        duration_ms=duration,
                    ),
                    ToolResultBlock(
                        call_id=tu.call_id,
                        content=[ErrorBlock(error_type="ApprovalDenied", message=err)],
                        is_error=True,
                    ),
                )

        await self.hooks.dispatch(
            HookEvent.TOOL_START,
            {"agent": self.name, "tool": tu.tool_name, "args": dict(tu.arguments)},
        )
        try:
            if self.tool_timeout is not None:
                exec_result = await asyncio.wait_for(
                    tool.execute(**tu.arguments), timeout=self.tool_timeout
                )
            else:
                exec_result = await tool.execute(**tu.arguments)

            duration = (time.monotonic() - t0) * 1000
            await self.hooks.dispatch(
                HookEvent.TOOL_END,
                {
                    "agent": self.name,
                    "tool": tu.tool_name,
                    "is_error": exec_result.is_error,
                    "duration_ms": duration,
                },
            )
            return (
                ToolCallRecord(
                    name=tu.tool_name,
                    call_id=tu.call_id,
                    arguments=dict(tu.arguments),
                    result=exec_result.text,
                    is_error=exec_result.is_error,
                    duration_ms=duration,
                ),
                ToolResultBlock(
                    call_id=tu.call_id,
                    content=exec_result.content,
                    is_error=exec_result.is_error,
                ),
            )

        except asyncio.TimeoutError:
            duration = (self.tool_timeout or 0.0) * 1000
            err = f"Tool '{tu.tool_name}' timed out after {self.tool_timeout}s"
            await self.hooks.dispatch(
                HookEvent.TOOL_END,
                {"agent": self.name, "tool": tu.tool_name, "is_error": True},
            )
            return (
                ToolCallRecord(
                    name=tu.tool_name,
                    call_id=tu.call_id,
                    arguments=dict(tu.arguments),
                    result=err,
                    is_error=True,
                    duration_ms=duration,
                ),
                ToolResultBlock(
                    call_id=tu.call_id,
                    content=[ErrorBlock(error_type="TimeoutError", message=err)],
                    is_error=True,
                ),
            )

        except Exception as exc:
            duration = (time.monotonic() - t0) * 1000
            err = f"Tool '{tu.tool_name}' raised {type(exc).__name__}: {exc}"
            logger.exception("[%s] tool %s failed", self.name, tu.tool_name)
            await self.hooks.dispatch(
                HookEvent.TOOL_END,
                {"agent": self.name, "tool": tu.tool_name, "is_error": True},
            )
            return (
                ToolCallRecord(
                    name=tu.tool_name,
                    call_id=tu.call_id,
                    arguments=dict(tu.arguments),
                    result=err,
                    is_error=True,
                    duration_ms=duration,
                ),
                ToolResultBlock(
                    call_id=tu.call_id,
                    content=[
                        ErrorBlock(error_type=type(exc).__name__, message=str(exc))
                    ],
                    is_error=True,
                ),
            )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _content_to_str(content: list[ContentBlock]) -> str:
    """Extract concatenated plain text from a content block list."""
    return " ".join(b.text for b in content if isinstance(b, TextBlock) and b.text)


async def _stream_generate(
    model: LLMClient,
    messages: list[ChatMessage],
    system: str,
    tools: list[dict[str, object]] | None = None,
) -> AsyncIterator[TextDelta | ReasoningDelta | CompletionEvent]:
    """Normalize generate_stream to an async generator.

    Handles both async-generator implementations and coroutine-returning-iterator
    implementations of LLMClient.generate_stream.
    """
    import inspect

    result = model.generate_stream(messages, tools=tools, system=system)
    if inspect.isawaitable(result):
        result = await result
    async for event in result:
        yield event
