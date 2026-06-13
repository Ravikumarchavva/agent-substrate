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
import json
import time
import itertools
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncIterator
from uuid import uuid4

if TYPE_CHECKING:
    from ravi.agents.supervision.budget import SpawnBudget


from ravi.kernel import (
    AgentId,
    ChatMessage,
    ContentBlock,
    ErrorBlock,
    RunContext,
    Supervision,
    TextBlock,
    Tool,
    ToolExecutionResult,
    ToolResultBlock,
    ToolRisk,
    ToolUI,
    ToolUseBlock,
    UIResourceBlock,
    AgentCrashError,
    JsonObject,
)
from ravi.kernel.messaging.message import MessageContext, RuntimeRef
from ravi.kernel.tools.approval import ApprovalHandler
from ravi.kernel.llm import GenerationOptions
from ravi.kernel.messaging.stream import (
    AgentProgress,
    AgentStep,
    CompletionEvent,
    ReasoningDelta,
    StreamDone,
    TextDelta,
)
from ravi.agents.context import AgentContext, ContextConfig, HistoryProvider
from ravi.kernel import Skill
from ravi.kernel.llm import LLMClient, LLMResponse, Usage
from ravi.agents.hooks.manager import HookEvent, HookManager
from ravi.agents.resources.budget import BudgetExceededError, ExecutionTracker
from ravi.agents.middleware._contracts import (
    AgentRunContext,
    AgentRunResult,
    ChatContext,
    FunctionContext,
    ToolCallRecord,
)
from ravi.agents.middleware.pipeline import MiddlewarePipeline
from ravi.kernel.agent.middleware import (
    AgentMiddleware,
    ChatMiddleware,
    FunctionMiddleware,
)
from ravi.kernel.core.errors import MiddlewareTermination
from ravi.logger import setup_logging

logger = setup_logging()


@dataclass
class _Finished:
    result: AgentRunResult


# ---------------------------------------------------------------------------
# ReActAgent
# ---------------------------------------------------------------------------


class ReActAgent:
    """Full ReAct agent on the kernel/fabric model.

    Usage::

        from ravi.integrations.llm.openai import OpenAIClient
        from ravi.agents.runtime import LocalRuntime
        from ravi.agents.context import ContextConfig, SlidingWindowCompaction
        from ravi.capabilities.history import RedisHistoryProvider
        from ravi.kernel import Skill

        runtime = LocalRuntime()
        await runtime.start()

        agent = ReActAgent(
            "researcher", runtime,
            model=OpenAIClient(model="gpt-4o"),
            tools=[calc_tool],
            skills=[Skill(name="math", instructions="Show your working step by step.")],
            system_instructions="You are a helpful AI assistant.",
            context=ContextConfig(
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
        runtime: Any,
        *,
        model: LLMClient,
        description: str = "",
        tools: list[Tool] | None = None,
        skills: list[Skill] | None = None,
        system_instructions: str | None = None,
        max_iterations: int = 20,
        tool_timeout: float | None = 30.0,
        context: ContextConfig | None = None,
        agent_middleware: list[AgentMiddleware] | None = None,
        chat_middleware: list[ChatMiddleware] | None = None,
        function_middleware: list[FunctionMiddleware] | None = None,
        approval_handler: ApprovalHandler | None = None,
        approval_required_risk: ToolRisk = ToolRisk.HIGH,
        hooks: HookManager | None = None,
        supervision: Supervision | None = None,
        execution_budget: ExecutionTracker | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.runtime = runtime
        self.id = AgentId(type="assistant", key=name)
        self.supervision: Supervision | None = supervision
        self.model = model
        self._tools: dict[str, Tool] = {t.name: t for t in (tools or [])}
        self._skills: list[Skill] = list(skills or [])
        self._base_system = system_instructions or self._DEFAULT_SYSTEM
        self.max_iterations = max_iterations
        self.tool_timeout = tool_timeout
        self.agent_pipeline = MiddlewarePipeline(agent_middleware)
        self.chat_pipeline = MiddlewarePipeline(chat_middleware)
        self.function_pipeline = MiddlewarePipeline(function_middleware)
        self._approval_handler = approval_handler
        self._approval_required_risk = approval_required_risk
        self.hooks = hooks or HookManager()
        self.execution_budget: ExecutionTracker | None = execution_budget

        # Set by the orchestrator when this agent is a subagent in a supervised tree.
        # Checked before each LLM call; if True, agent pauses cooperatively.
        self.spawn_budget: SpawnBudget | None = None

        _cfg = context if context is not None else ContextConfig.default()
        self._ctx = AgentContext(self.id, _cfg.history, _cfg.compaction)

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

    @property
    def tools(self) -> list[Tool]:
        """Public property — returns all registered tools."""
        return list(self._tools.values())

    # -- Actor entry point ---------------------------------------------------

    async def on_message(self, _ctx: MessageContext, payload: object) -> object:
        """Actor runtime entry point — delegates to run()."""
        if isinstance(payload, str):
            return await self.run(payload)
        if isinstance(payload, list):
            text = " ".join(b.text for b in payload if isinstance(b, TextBlock))
            return await self.run(text)
        return None

    async def bind(self, runtime: RuntimeRef) -> None:
        self.runtime = runtime

    async def save_state(self) -> JsonObject:
        return {"name": self.name, "session_id": self.id.key}

    async def load_state(self, state: JsonObject) -> None:
        pass

    # -- Non-streaming run ---------------------------------------------------

    async def _react(
        self,
        input_text: str,
        *,
        session_id: str | None = None,
        resume: bool = False,
        run_id: str | None = None,
        stream: bool = False,
        initial_tool_choice: str | None = None,
    ) -> AsyncIterator[TextDelta | ReasoningDelta | CompletionEvent | _Finished]:
        # -- History scope (the conversation thread) ---------------------------
        if session_id is not None:
            sid = session_id
        elif self.supervision is not None:
            sid = self.supervision.session_id
        else:
            sid = self.id.key  # stable standalone thread

        # -- Execution scope (this turn) ---------------------------------------
        if run_id is not None:
            rid = run_id
        elif self.supervision is not None:
            rid = self.supervision.run_id
        else:
            rid = uuid4().hex

        _seq = itertools.count()
        run_ctx = RunContext.standalone(trace_id=rid)

        tool_calls: list[ToolCallRecord] = []

        await self.hooks.dispatch(
            HookEvent.RUN_START,
            {
                "agent": self.name,
                "run_id": rid,
                "session_id": sid,
                "input": input_text[:80],
            },
        )
        await self._emit_progress(
            AgentStep.STARTED, input_text[:120], rid, seq=next(_seq)
        )
        logger.info(
            "[%s] run start (resume=%s, stream=%s, session=%s): %.80s",
            self.name,
            resume,
            stream,
            sid,
            input_text,
        )

        if not resume:
            await self._append(
                ChatMessage(role="user", content=[TextBlock(text=input_text)]),
                sid,
                rid,
            )

        agent_ctx = AgentRunContext(
            agent_name=self.name,
            run_id=rid,
            session_id=sid,
            messages=[ChatMessage(role="user", content=[TextBlock(text=input_text)])],
        )

        _event_q: asyncio.Queue[Any] = asyncio.Queue()

        async def _core_loop(ctx: AgentRunContext) -> None:
            try:
                for step in range(1, self.max_iterations + 1):
                    # -- cooperative cancellation check -----------------------------
                    run_ctx.check()

                    # -- cooperative pause check ------------------------------------
                    if self.spawn_budget is not None and self.spawn_budget.is_paused(
                        self.id
                    ):
                        logger.info(
                            "[%s] paused by priority preemption at step %d",
                            self.name,
                            step,
                        )
                        await self._emit_progress(
                            AgentStep.PAUSED,
                            "paused by priority preemption",
                            rid,
                            seq=next(_seq),
                        )
                        await self.hooks.dispatch(
                            HookEvent.RUN_END,
                            {"agent": self.name, "run_id": rid, "status": "paused"},
                        )
                        last_output = await self._last_assistant_text(sid)
                        await _event_q.put(
                            _Finished(
                                AgentRunResult(
                                    output=last_output,
                                    status="paused",
                                    tool_calls=tool_calls,
                                    run_id=rid,
                                )
                            )
                        )
                        return

                    await self.hooks.dispatch(
                        HookEvent.STEP_START, {"agent": self.name, "step": step}
                    )
                    messages = await self._prompt_window(sid)

                    # -- cooperative cancellation check -----------------------------
                    run_ctx.check()

                    await self.hooks.dispatch(
                        HookEvent.LLM_START,
                        {
                            "agent": self.name,
                            "step": step,
                            "message_count": len(messages),
                        },
                    )
                    await self._emit_progress(
                        AgentStep.THINKING, f"step {step}", rid, seq=next(_seq)
                    )

                    # -- ExecutionTracker: count prompt tokens for cost estimation --
                    if self.execution_budget is not None:
                        try:
                            prompt_tokens = await self.model.count_tokens(messages)
                        except Exception:
                            prompt_tokens = 0

                    content: list[ContentBlock] = []

                    turn_usage: Usage = Usage()

                    chat_ctx = ChatContext(
                        agent_name=self.name,
                        run_id=rid,
                        messages=messages,
                        system_instructions=self._system,
                        tools=self.list_tools() or None,
                        result=None,
                    )

                    async def _do_chat(c: ChatContext) -> None:

                        nonlocal content, turn_usage

                        tool_choice = initial_tool_choice if step == 1 else None
                        opts = GenerationOptions(
                            tools=self.list_tools() or None,
                            system_instructions=self._system,
                            tool_choice=tool_choice,
                        )

                        if stream:
                            async for event in self.model.generate_stream(
                                messages, options=opts
                            ):
                                if isinstance(event, (TextDelta, ReasoningDelta)):
                                    stamped = event.model_copy(
                                        update={
                                            "seq": next(_seq),
                                            "agent_id": self.id,
                                            "run_id": rid,
                                        }
                                    )
                                    await _event_q.put(stamped)
                                elif isinstance(event, CompletionEvent):
                                    content = event.content
                                    turn_usage = event.usage
                        else:
                            resp = await self.model.generate(messages, options=opts)
                            content = resp.content
                            turn_usage = resp.usage

                        c.result = LLMResponse(content=content, usage=turn_usage)

                    await self.chat_pipeline.execute(chat_ctx, _do_chat)

                    content = chat_ctx.result.content if chat_ctx.result else content

                    turn_usage = (
                        chat_ctx.result.usage if chat_ctx.result else turn_usage
                    )

                    # -- ExecutionTracker: record this turn's usage -----------------
                    if self.execution_budget is not None:
                        self.execution_budget.consume(
                            tokens=turn_usage.total_tokens or prompt_tokens, turns=1
                        )

                    await self.hooks.dispatch(
                        HookEvent.LLM_END, {"agent": self.name, "step": step}
                    )

                    if not content:
                        break

                    tool_uses = [b for b in content if isinstance(b, ToolUseBlock)]
                    await self._append(
                        ChatMessage(role="assistant", content=content), sid, rid
                    )

                    if not tool_uses:
                        output = _content_to_str(content)

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
                            AgentStep.DONE, output[:120], rid, seq=next(_seq)
                        )
                        if stream:
                            await _event_q.put(
                                CompletionEvent(
                                    content=content,
                                    seq=next(_seq),
                                    agent_id=self.id,
                                    run_id=rid,
                                    usage=turn_usage,
                                )
                            )

                        await _event_q.put(
                            _Finished(
                                AgentRunResult(
                                    output=output,
                                    status="success",
                                    tool_calls=tool_calls,
                                    run_id=rid,
                                )
                            )
                        )
                        return

                    logger.info(
                        "[%s] step %d: tools → %s",
                        self.name,
                        step,
                        [b.tool_name for b in tool_uses],
                    )
                    result_blocks: list[ContentBlock] = []

                    # Phase 1: emit TOOL_CALL progress + run guardrails (sequential — fast)
                    for tu in tool_uses:
                        call_progress = await self._emit_progress(
                            AgentStep.TOOL_CALL,
                            tu.tool_name,
                            rid,
                            seq=next(_seq),
                            call_id=tu.call_id,
                            tool_args=json.dumps(dict(tu.arguments)),
                        )
                        if stream and call_progress is not None:
                            await _event_q.put(call_progress)

                    # Phase 2: run all tools concurrently; await _event_q.put(progress as each event arrives.)
                    # progress_q receives subagent AgentProgress events (from _DispatchTool).
                    # done_q receives tool completion tuples.
                    progress_q: asyncio.Queue[AgentProgress] = asyncio.Queue()
                    done_q: asyncio.Queue[
                        tuple[ToolUseBlock, ToolCallRecord, ContentBlock]
                    ] = asyncio.Queue()

                    # Wire progress sink into any tool that supports it (_DispatchTool)
                    for tool in self._tools.values():
                        if hasattr(tool, "set_progress_sink"):
                            tool.set_progress_sink(progress_q)

                    async def _run(t: ToolUseBlock) -> None:
                        r, b = await self._execute_tool(
                            t,
                            rid,
                            run_ctx=run_ctx,
                            seq_counter=_seq,
                            event_sink=_event_q if stream else None,
                        )
                        await done_q.put((t, r, b))

                    tasks = [asyncio.create_task(_run(tu)) for tu in tool_uses]

                    results_by_call_id: dict[
                        str, tuple[ToolCallRecord, ContentBlock]
                    ] = {}
                    completed = 0
                    while completed < len(tool_uses):
                        # Drain any buffered subagent progress events (non-blocking)
                        while not progress_q.empty():
                            sub_event = progress_q.get_nowait()
                            if stream:
                                await _event_q.put(sub_event)

                        # Wait up to 50 ms for the next tool completion, then loop back
                        # to drain progress again so subagent events appear promptly.
                        try:
                            tu_done, record, block = await asyncio.wait_for(
                                done_q.get(), timeout=0.05
                            )
                        except asyncio.TimeoutError:
                            continue

                        results_by_call_id[tu_done.call_id] = (record, block)
                        ui_meta: dict[str, str] = {}
                        if isinstance(block, ToolResultBlock):
                            ui_block = next(
                                (
                                    b
                                    for b in block.content
                                    if isinstance(b, UIResourceBlock)
                                ),
                                None,
                            )
                            if ui_block is not None:
                                ui_meta["ui"] = ui_block.model_dump_json()
                        result_progress = await self._emit_progress(
                            AgentStep.TOOL_RESULT,
                            f"{tu_done.tool_name}: {'error' if record.is_error else 'ok'}",
                            rid,
                            seq=next(_seq),
                            call_id=tu_done.call_id,
                            **ui_meta,
                        )
                        if stream and result_progress is not None:
                            await _event_q.put(result_progress)
                        completed += 1

                    # Final drain — any events emitted right before task completion
                    # (Also stamp them since they originate from subagents)
                    while not progress_q.empty():
                        sub_event = progress_q.get_nowait()
                        if stream:
                            await _event_q.put(sub_event)

                    await asyncio.gather(*tasks)  # ensure all tasks are fully settled

                    # Unwire sinks
                    for tool in self._tools.values():
                        if hasattr(tool, "set_progress_sink"):
                            tool.set_progress_sink(None)

                    # Reconstruct in original call order for the tool message
                    for tu in tool_uses:
                        record, block = results_by_call_id[tu.call_id]
                        tool_calls.append(record)
                        result_blocks.append(block)

                    await self._append(
                        ChatMessage(role="tool", content=result_blocks), sid, rid
                    )
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
                    AgentStep.DONE,
                    last_output[:120],
                    rid,
                    seq=next(_seq),
                    status="max_iterations",
                )
                await _event_q.put(
                    _Finished(
                        AgentRunResult(
                            output=last_output,
                            status="max_iterations",
                            tool_calls=tool_calls,
                            run_id=rid,
                        )
                    )
                )
                return

            except MiddlewareTermination as exc:
                logger.warning("[%s] guardrail tripped: %s", self.name, exc.message)
                await self.hooks.dispatch(
                    HookEvent.RUN_END,
                    {"agent": self.name, "run_id": rid, "status": "guardrail_tripped"},
                )
                await self._emit_progress(
                    AgentStep.ERROR, f"guardrail: {exc.message}", rid, seq=next(_seq)
                )
                await _event_q.put(
                    _Finished(
                        AgentRunResult(
                            output=f"Request blocked: {exc.message}",
                            status="guardrail_tripped",
                            tool_calls=tool_calls,
                            run_id=rid,
                            error=exc.message,
                        )
                    )
                )
                return

            except BudgetExceededError as exc:
                logger.warning("[%s] budget exceeded: %s", self.name, exc)
                await self._emit_progress(
                    AgentStep.ERROR, f"budget: {exc}", rid, seq=next(_seq)
                )
                await self.hooks.dispatch(
                    HookEvent.RUN_END,
                    {"agent": self.name, "run_id": rid, "status": "budget_exceeded"},
                )
                await _event_q.put(
                    _Finished(
                        AgentRunResult(
                            output="",
                            status="error",
                            tool_calls=tool_calls,
                            run_id=rid,
                            error=str(exc),
                        )
                    )
                )
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
                    AgentStep.ERROR,
                    f"crash: {type(exc).__name__}: {exc}",
                    rid,
                    seq=next(_seq),
                )
                raise AgentCrashError(
                    f"Agent '{self.name}' crashed: {exc}",
                    run_id=rid,
                    agent_id=self.id,
                ) from exc

        async def _run_pipelines() -> None:
            try:
                await self.agent_pipeline.execute(agent_ctx, _core_loop)
            except MiddlewareTermination as exc:
                logger.warning(
                    "[%s] agent middleware terminated: %s", self.name, exc.message
                )
                await self.hooks.dispatch(
                    HookEvent.RUN_END,
                    {"agent": self.name, "run_id": rid, "status": "guardrail_tripped"},
                )
                await _event_q.put(
                    _Finished(
                        AgentRunResult(
                            output=f"Request blocked: {exc.message}",
                            status="guardrail_tripped",
                            tool_calls=tool_calls,
                            run_id=rid,
                            error=exc.message,
                        )
                    )
                )
            except Exception as _e:
                await _event_q.put(_e)
            finally:
                await _event_q.put(StopAsyncIteration)

        asyncio.create_task(_run_pipelines())

        while True:
            item = await _event_q.get()

            if item is StopAsyncIteration:
                break

            if isinstance(item, Exception):
                raise item

            yield item

    async def run(
        self,
        input_text: str,
        *,
        session_id: str | None = None,
        resume: bool = False,
        run_id: str | None = None,
        initial_tool_choice: str | None = None,
    ) -> AgentRunResult:
        """Execute the ReAct loop to completion."""
        async for event in self._react(
            input_text,
            session_id=session_id,
            resume=resume,
            run_id=run_id,
            stream=False,
            initial_tool_choice=initial_tool_choice,
        ):
            if isinstance(event, _Finished):
                return event.result
        return AgentRunResult(
            output="", status="error", error="Stream ended without result"
        )

    async def run_stream(
        self,
        input_text: str,
        *,
        session_id: str | None = None,
        resume: bool = False,
        run_id: str | None = None,
        initial_tool_choice: str | None = None,
    ) -> AsyncIterator[TextDelta | ReasoningDelta | CompletionEvent | StreamDone]:
        """Execute the ReAct loop, yielding progress events."""
        try:
            async for event in self._react(
                input_text,
                session_id=session_id,
                resume=resume,
                run_id=run_id,
                stream=True,
                initial_tool_choice=initial_tool_choice,
            ):
                if isinstance(event, _Finished):
                    yield StreamDone(
                        reason="success"
                        if event.result.status == "success"
                        else event.result.status
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
        self, step: str, content: str, run_id: str, seq: int = 0, **meta: str
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
        from ravi.kernel.core.identity import TopicId

        sv = self.supervision
        topic_source = run_id if run_id else self.id.key
        event = AgentProgress(
            agent_id=self.id,
            step=step,
            content=content,
            run_id=run_id,
            parent_id=sv.parent_id if sv is not None else None,
            depth=sv.depth if sv is not None else 0,
            seq=seq,
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

    async def _append(
        self, chat_msg: ChatMessage, session_id: str, run_id: str
    ) -> None:
        await self._ctx.history.append(
            self.id, chat_msg, session_id=session_id, run_id=run_id
        )

    async def _prompt_window(self, session_id: str) -> list[ChatMessage]:
        return await self._ctx.get_prompt_window(session_id)

    async def _last_assistant_text(self, session_id: str) -> str:
        window = await self._ctx.get_prompt_window(session_id)
        for msg in reversed(window):
            if msg.role == "assistant":
                return _content_to_str(msg.content)
        return ""

    @staticmethod
    def _lower_ui(tool: Tool, exec_result: ToolExecutionResult) -> list[ContentBlock]:
        """Append a UIResourceBlock when the tool renders through an MCP-App UI.

        If the tool declares a ``ToolUI`` and produced ``structured_content``,
        lower the declaration + data into a self-describing ``UIResourceBlock``
        so the wire, history, and renderer share one carrier.  The block's
        ``text`` stays empty — the model already reads ``exec_result.content``;
        the block adds only a ``[interactive UI: …]`` marker.  Tools that return
        a ``UIResourceBlock`` directly are passed through untouched.
        """
        content = list(exec_result.content)
        ui: ToolUI | None = getattr(tool, "ui", None)
        already = any(isinstance(b, UIResourceBlock) for b in content)
        if ui is not None and exec_result.structured_content and not already:
            content.append(
                UIResourceBlock(
                    uri=ui.resource_uri,
                    structured_content=exec_result.structured_content,
                )
            )
        return content

    async def _execute_tool(
        self,
        tu: ToolUseBlock,
        rid: str = "",
        run_ctx: RunContext | None = None,
        seq_counter: itertools.count | None = None,
        event_sink: asyncio.Queue[Any] | None = None,
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
            from ravi.kernel.tools.approval import ApprovalRequest, ApprovalDecision
            from ravi.kernel.tools import ToolCallRequest

            paused_progress = await self._emit_progress(
                AgentStep.PAUSED,
                f"Awaiting approval for {tu.tool_name}",
                rid,
                seq=next(seq_counter) if seq_counter is not None else 0,
                reason="hitl_approval",
            )
            if event_sink is not None and paused_progress is not None:
                await event_sink.put(paused_progress)

            if hasattr(self._approval_handler, "request"):
                decision = await self._approval_handler.request(
                    ApprovalRequest(
                        call=ToolCallRequest(
                            name=tu.tool_name, arguments=dict(tu.arguments)
                        ),
                        risk=tool_risk,
                        agent_id=self.id,
                        run_id=rid,
                    )
                )
                approved = decision == ApprovalDecision.APPROVED
            else:
                # Backwards compatibility for legacy callable handlers (e.g. tests)
                approved = await self._approval_handler(
                    tu.tool_name, dict(tu.arguments)
                )  # type: ignore
                decision = (
                    ApprovalDecision.APPROVED if approved else ApprovalDecision.DENIED
                )

            thinking_progress = await self._emit_progress(
                AgentStep.THINKING,
                f"Approval decision: {decision}. Resuming.",
                rid,
                seq=next(seq_counter) if seq_counter is not None else 0,
            )
            if event_sink is not None and thinking_progress is not None:
                await event_sink.put(thinking_progress)

            if not approved:
                duration = (time.monotonic() - t0) * 1000
                err = f"Tool '{tu.tool_name}' denied by approval handler: {decision}"
                logger.info(
                    "[%s] HITL denied: %s (decision=%s)",
                    self.name,
                    tu.tool_name,
                    decision,
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
                        content=[ErrorBlock(error_type="ApprovalDenied", message=err)],
                        is_error=True,
                    ),
                )

        await self.hooks.dispatch(
            HookEvent.TOOL_START,
            {"agent": self.name, "tool": tu.tool_name, "args": dict(tu.arguments)},
        )
        try:
            func_ctx = FunctionContext(
                agent_name=self.name,
                run_id=rid,
                function_name=tu.tool_name,
                arguments=dict(tu.arguments),
                result=None,
            )

            async def _do_function(c: FunctionContext) -> None:
                if run_ctx is not None:
                    run_ctx.check()

                if self.tool_timeout is not None:
                    c.result = await asyncio.wait_for(
                        tool.execute(**c.arguments), timeout=self.tool_timeout
                    )

                else:
                    c.result = await tool.execute(**c.arguments)

            await self.function_pipeline.execute(func_ctx, _do_function)

            exec_result = func_ctx.result

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
                    content=self._lower_ui(tool, exec_result),
                    is_error=exec_result.is_error,
                ),
            )
        except MiddlewareTermination:
            raise

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
