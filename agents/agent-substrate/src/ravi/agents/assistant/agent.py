"""AssistantAgent — ReAct loop built on the new kernel/fabric model.

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
    TextBlock,
    Tool,
    ToolResultBlock,
    ToolRisk,
    ToolUseBlock,
)
from ravi.kernel.message import Message
from ravi.kernel.stream import CompletionEvent, ReasoningDelta, StreamDone, TextDelta
from ravi.agents.context import (
    AgentContext,
    HistoryProvider,
    InMemoryHistoryProvider,
    SlidingWindowCompaction,
)
from ravi.agents.context.context import DefaultAgentContext
from ravi.kernel.llm.client import LLMClient
from ravi.agents.hooks.manager import HookEvent, HookManager
from ravi.agents.assistant._guardrail_runner import (
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
class AgentRunResult:
    """Result of a completed agent run."""

    output: str
    status: str  # "success" | "error" | "max_iterations" | "guardrail_tripped"
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
# AssistantAgent
# ---------------------------------------------------------------------------


class AssistantAgent:
    """Full ReAct agent on the kernel/fabric model.

    Usage::

        from ravi.adapters.llm.openai import OpenAIClient
        from ravi.fabric.runtime.local import LocalRuntime

        runtime = LocalRuntime()
        await runtime.start()

        agent = AssistantAgent(
            "researcher", runtime,
            model=OpenAIClient(model="gpt-4o"),
            tools=[calc_tool],
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
        system: str | None = None,
        max_iterations: int = 20,
        tool_timeout: float | None = 30.0,
        history: HistoryProvider | None = None,
        compaction: SlidingWindowCompaction | None = None,
        context: AgentContext | None = None,
        guardrails: list[object] | None = None,
        approval_handler: ApprovalHandler | None = None,
        approval_required_risk: ToolRisk = ToolRisk.HIGH,
        hooks: HookManager | None = None,
    ) -> None:
        self.name = name
        self.runtime = runtime
        self.id = AgentId(type="assistant", key=name)
        self.model = model
        self._tools: dict[str, Tool] = {t.name: t for t in (tools or [])}
        self._system = system or self._DEFAULT_SYSTEM
        self.max_iterations = max_iterations
        self.tool_timeout = tool_timeout
        self._guardrails: list[object] = list(guardrails or [])
        self._approval_handler = approval_handler
        self._approval_required_risk = approval_required_risk
        self.hooks = hooks or HookManager()

        if context is not None:
            _hist = context.history
            _comp = context.compaction
        else:
            _hist = history or InMemoryHistoryProvider()
            _comp = compaction or SlidingWindowCompaction(max_messages=40)
        self._ctx = DefaultAgentContext(self.id, _hist, _comp)

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

    async def run(self, input_text: str) -> AgentRunResult:
        """Execute the ReAct loop to completion."""
        run_id = uuid4().hex
        tool_calls: list[ToolCallRecord] = []

        await self.hooks.dispatch(
            HookEvent.RUN_START,
            {"agent": self.name, "run_id": run_id, "input": input_text[:80]},
        )
        logger.info("[%s] run start: %.80s", self.name, input_text)

        await self._append(ChatMessage(role="user", content=[TextBlock(text=input_text)]))

        try:
            # -- input guardrails ------------------------------------------------
            if self._guardrails:
                await check_input_guardrails(
                    guardrails=self._guardrails,
                    agent_name=self.name,
                    run_id=run_id,
                    input_text=input_text,
                )

            for step in range(1, self.max_iterations + 1):
                await self.hooks.dispatch(
                    HookEvent.STEP_START, {"agent": self.name, "step": step}
                )
                messages = await self._prompt_window()

                await self.hooks.dispatch(
                    HookEvent.LLM_START,
                    {"agent": self.name, "step": step, "message_count": len(messages)},
                )
                content = await self.model.generate(
                    messages, tools=self._tool_schemas(), system=self._system
                )
                await self.hooks.dispatch(
                    HookEvent.LLM_END, {"agent": self.name, "step": step}
                )

                tool_uses = [b for b in content if isinstance(b, ToolUseBlock)]
                await self._append(ChatMessage(role="assistant", content=content))

                if not tool_uses:
                    output = _content_to_str(content)

                    # -- output guardrails ---------------------------------------
                    if self._guardrails:
                        await check_output_guardrails(
                            guardrails=self._guardrails,
                            agent_name=self.name,
                            run_id=run_id,
                            output_text=output,
                        )

                    logger.info("[%s] done at step %d", self.name, step)
                    await self.hooks.dispatch(
                        HookEvent.STEP_END,
                        {"agent": self.name, "step": step, "has_tool_calls": False},
                    )
                    await self.hooks.dispatch(
                        HookEvent.RUN_END,
                        {"agent": self.name, "run_id": run_id, "status": "success"},
                    )
                    return AgentRunResult(
                        output=output,
                        status="success",
                        tool_calls=tool_calls,
                        run_id=run_id,
                    )

                logger.info(
                    "[%s] step %d: tools → %s",
                    self.name,
                    step,
                    [b.tool_name for b in tool_uses],
                )
                result_blocks: list[ContentBlock] = []
                for tu in tool_uses:
                    # -- tool-call guardrails ------------------------------------
                    if self._guardrails:
                        await check_tool_call_guardrails(
                            guardrails=self._guardrails,
                            agent_name=self.name,
                            run_id=run_id,
                            tool_use=tu,
                        )
                    record, block = await self._execute_tool(tu)
                    tool_calls.append(record)
                    result_blocks.append(block)

                await self._append(ChatMessage(role="tool", content=result_blocks))
                await self.hooks.dispatch(
                    HookEvent.STEP_END,
                    {"agent": self.name, "step": step, "has_tool_calls": True},
                )

            logger.warning("[%s] hit max_iterations (%d)", self.name, self.max_iterations)
            last_output = await self._last_assistant_text()
            await self.hooks.dispatch(
                HookEvent.RUN_END,
                {"agent": self.name, "run_id": run_id, "status": "max_iterations"},
            )
            return AgentRunResult(
                output=last_output,
                status="max_iterations",
                tool_calls=tool_calls,
                run_id=run_id,
            )

        except GuardrailTripwireError as exc:
            logger.warning("[%s] guardrail tripped: %s", self.name, exc.message)
            await self.hooks.dispatch(
                HookEvent.RUN_END,
                {"agent": self.name, "run_id": run_id, "status": "guardrail_tripped"},
            )
            return AgentRunResult(
                output=f"Request blocked: {exc.message}",
                status="guardrail_tripped",
                tool_calls=tool_calls,
                run_id=run_id,
                error=exc.message,
            )

        except Exception as exc:
            logger.exception("[%s] run failed: %s", self.name, exc)
            await self.hooks.dispatch(
                HookEvent.RUN_END,
                {"agent": self.name, "run_id": run_id, "status": "error", "error": str(exc)},
            )
            return AgentRunResult(
                output="",
                status="error",
                tool_calls=tool_calls,
                run_id=run_id,
                error=str(exc),
            )

    # -- Streaming run -------------------------------------------------------

    async def run_stream(
        self, input_text: str
    ) -> AsyncIterator[TextDelta | ReasoningDelta | CompletionEvent | StreamDone]:
        """Execute the ReAct loop, yielding progress events.

        Yields ``TextDelta`` / ``ReasoningDelta`` as tokens arrive, then
        ``CompletionEvent`` with the full assembled content on the final turn,
        and ``StreamDone`` as the end-of-stream sentinel.
        """
        run_id = uuid4().hex
        logger.info("[%s] stream start: %.80s", self.name, input_text)

        await self._append(ChatMessage(role="user", content=[TextBlock(text=input_text)]))

        await self.hooks.dispatch(
            HookEvent.RUN_START,
            {"agent": self.name, "run_id": run_id, "input": input_text[:80]},
        )

        try:
            # -- input guardrails ------------------------------------------------
            if self._guardrails:
                await check_input_guardrails(
                    guardrails=self._guardrails,
                    agent_name=self.name,
                    run_id=run_id,
                    input_text=input_text,
                )

            for step in range(1, self.max_iterations + 1):
                messages = await self._prompt_window()
                content: list[ContentBlock] = []

                async for event in _stream_generate(
                    self.model, messages, self._system, self._tool_schemas()
                ):
                    if isinstance(event, (TextDelta, ReasoningDelta)):
                        yield event
                    elif isinstance(event, CompletionEvent):
                        content = event.content

                if not content:
                    break

                tool_uses = [b for b in content if isinstance(b, ToolUseBlock)]
                await self._append(ChatMessage(role="assistant", content=content))

                if not tool_uses:
                    output = _content_to_str(content)

                    # -- output guardrails ---------------------------------------
                    if self._guardrails:
                        await check_output_guardrails(
                            guardrails=self._guardrails,
                            agent_name=self.name,
                            run_id=run_id,
                            output_text=output,
                        )

                    yield CompletionEvent(content=content)
                    await self.hooks.dispatch(
                        HookEvent.RUN_END,
                        {"agent": self.name, "run_id": run_id, "status": "success"},
                    )
                    break

                logger.info(
                    "[%s] [stream] step %d: tools → %s",
                    self.name,
                    step,
                    [b.tool_name for b in tool_uses],
                )
                result_blocks: list[ContentBlock] = []
                for tu in tool_uses:
                    # -- tool-call guardrails ------------------------------------
                    if self._guardrails:
                        await check_tool_call_guardrails(
                            guardrails=self._guardrails,
                            agent_name=self.name,
                            run_id=run_id,
                            tool_use=tu,
                        )
                    _, block = await self._execute_tool(tu)
                    result_blocks.append(block)

                await self._append(ChatMessage(role="tool", content=result_blocks))

            else:
                logger.warning("[%s] [stream] hit max_iterations", self.name)
                await self.hooks.dispatch(
                    HookEvent.RUN_END,
                    {"agent": self.name, "run_id": run_id, "status": "max_iterations"},
                )

        except GuardrailTripwireError as exc:
            logger.warning("[%s] [stream] guardrail tripped: %s", self.name, exc.message)
            await self.hooks.dispatch(
                HookEvent.RUN_END,
                {"agent": self.name, "run_id": run_id, "status": "guardrail_tripped"},
            )
            yield TextDelta(text=f"Request blocked: {exc.message}")

        except Exception as exc:
            logger.exception("[%s] stream run failed: %s", self.name, exc)
            await self.hooks.dispatch(
                HookEvent.RUN_END,
                {"agent": self.name, "run_id": run_id, "status": "error", "error": str(exc)},
            )

        yield StreamDone()

    # -- Internal helpers ----------------------------------------------------

    async def _append(self, chat_msg: ChatMessage) -> None:
        """Wrap a ChatMessage in a Message envelope and append to history."""
        envelope = Message(target=self.id, payload=chat_msg, sender=self.id)
        await self._ctx.history.append(self.id, envelope)

    async def _prompt_window(self) -> list[ChatMessage]:
        """Return the compacted history as ChatMessages for the LLM."""
        window = await self._ctx.get_prompt_window()
        return [
            m.payload
            for m in window
            if isinstance(m.payload, ChatMessage)
        ]

    async def _last_assistant_text(self) -> str:
        window = await self._ctx.get_prompt_window()
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
                    content=[ErrorBlock(error_type=type(exc).__name__, message=str(exc))],
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

    result = model.generate_stream(messages, tools=tools, system_instructions=system)
    if inspect.isawaitable(result):
        result = await result
    async for event in result:
        yield event
