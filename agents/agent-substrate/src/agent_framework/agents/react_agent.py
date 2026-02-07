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
from datetime import datetime
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple
from uuid import uuid4

from opentelemetry.trace import Status, StatusCode

from agent_framework.agents.base_agent import BaseAgent
from agent_framework.agents.agent_result import (
    AgentRunResult,
    AggregatedUsage,
    RunStatus,
    StepResult,
    ToolCallRecord,
)
from agent_framework.memory.base_memory import BaseMemory
from agent_framework.memory.unbounded_memory import UnboundedMemory
from agent_framework.messages.base_message import UsageStats
from agent_framework.messages.client_messages import (
    AssistantMessage,
    SystemMessage,
    ToolCallMessage,
    ToolExecutionResultMessage,
    UserMessage,
)
from agent_framework.model_clients.base_client import BaseModelClient
from agent_framework.observability import global_metrics, global_tracer, logger
from agent_framework.tools.base_tool import BaseTool, ToolResult


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
        max_iterations: int = 10,
        verbose: bool = True,
    ):
        super().__init__(
            name=name,
            description=description,
            model_client=model_client,
            tools=tools or [],
            system_instructions=system_instructions,
            memory=memory or UnboundedMemory(),
        )
        self.max_iterations = max_iterations
        self.verbose = verbose

        # Seed system prompt
        if len(self.memory.get_messages()) == 0:
            self.memory.add_message(SystemMessage(content=self.system_instructions))

    # ── Core run ─────────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Clear memory and return agent to initial state with system message."""
        super().reset()
        # Re-add system message after clearing
        self.memory.add_message(SystemMessage(content=self.system_instructions))

    async def run(self, input_text: str, **kwargs) -> AgentRunResult:
        run_start = datetime.utcnow()
        usage = AggregatedUsage()
        steps: List[StepResult] = []
        tool_calls_by_name: Dict[str, int] = {}
        total_tool_calls = 0
        status = RunStatus.COMPLETED
        error_msg: Optional[str] = None
        final_output: List[Any] = []  # Multimodal output

        attrs = {"agent_name": self.name, "input_length": len(input_text)}

        with global_tracer.start_span("agent_run", attrs) as run_span:
            global_metrics.increment_counter("agent_runs", tags={"name": self.name})
            if self.verbose:
                logger.info(f"[{self.name}] Starting run: {input_text[:80]}...")

            # 1. Add user message
            self.memory.add_message(UserMessage(content=[input_text]))

            # 2. ReAct loop
            for step_num in range(1, self.max_iterations + 1):
                with global_tracer.start_span(f"step_{step_num}", {"step": step_num}):

                    # A. THINK — call LLM
                    response = await self._call_llm(**kwargs)
                    usage.add(response.usage)
                    self.memory.add_message(response)

                    # Extract content (can be multimodal)
                    thought_content = response.content if response.content else None

                    # B. No tool calls → final answer
                    if not response.tool_calls:
                        if self.verbose:
                            logger.info(f"[{self.name}] Step {step_num}: final answer")
                        run_span.set_attribute("final_step", step_num)

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
                        record, tool_msg = await self._execute_tool(parsed, step_num)
                        self.memory.add_message(tool_msg)
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
            run_end = datetime.utcnow()
            duration = (run_end - run_start).total_seconds()

            return AgentRunResult(
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
            )

    # ── Streaming run ────────────────────────────────────────────────────────

    async def run_stream(self, input_text: str, **kwargs) -> AsyncIterator[Any]:
        """Streaming variant — yields partial chunks and tool results.

        For now this is a thin wrapper; a full streaming implementation
        can be added later with SSE / async generators.
        """
        attrs = {"agent_name": self.name, "input_length": len(input_text)}
        with global_tracer.start_span("agent_run_stream", attrs):
            global_metrics.increment_counter("agent_runs", tags={"name": self.name})
            if self.verbose:
                logger.info(f"[{self.name}] Starting streaming run: {input_text[:80]}...")

            self.memory.add_message(UserMessage(content=[input_text]))

            for step_num in range(1, self.max_iterations + 1):
                with global_tracer.start_span(f"step_{step_num}", {"step": step_num}):
                    # THINK
                    tool_schemas = self._build_tool_schemas()
                    messages = self.memory.get_messages()

                    with global_tracer.start_span("llm_generate_stream", {"msg_count": len(messages)}):
                        from agent_framework.messages._types import CompletionChunk
                        
                        llm_t0 = asyncio.get_event_loop().time()
                        final_response_obj = None

                        try:
                            async for chunk in self.model_client.generate_stream(
                                messages=messages,
                                tools=tool_schemas or None,
                                tool_choice="auto" if tool_schemas else None,
                                **kwargs,
                            ):
                                # Yield the chunk to user
                                yield chunk
                                
                                # Track final completion
                                if isinstance(chunk, CompletionChunk):
                                    final_response_obj = chunk.message
                            
                            # After stream completes, add final message to memory
                            if final_response_obj:
                                self.memory.add_message(final_response_obj)
                            
                            llm_t1 = asyncio.get_event_loop().time()
                            global_metrics.record_histogram(
                                "llm_latency", llm_t1 - llm_t0,
                                tags={"model": getattr(self.model_client, "model", "unknown")},
                            )
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
                        break

                    # ACT — execute tools
                    if self.verbose:
                        names = [self._parse_tool_call(tc).name for tc in response.tool_calls]
                        logger.info(f"[{self.name}] [stream] Step {step_num}: tools → {names}")

                    with global_tracer.start_span("execute_tools_stream", {"count": len(response.tool_calls)}):
                        for tc_raw in response.tool_calls:
                            parsed = self._parse_tool_call(tc_raw)
                            _, tool_msg = await self._execute_tool(parsed, step_num)
                            self.memory.add_message(tool_msg)
                            yield tool_msg

    # ── State management ─────────────────────────────────────────────────────

    def save_state(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "system_instructions": self.system_instructions,
            "max_iterations": self.max_iterations,
            "messages": [m.to_dict() for m in self.memory.get_messages()],
        }

    def load_state(self, state: Dict[str, Any]) -> None:
        if state.get("name") != self.name:
            logger.warning("Loading state for a different agent name")
        self.max_iterations = state.get("max_iterations", self.max_iterations)
        # TODO: reconstruct memory from state["messages"]

    # ── Private helpers ──────────────────────────────────────────────────────

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

    async def _call_llm(self, **kwargs) -> AssistantMessage:
        """Single LLM call with tool schemas and observability."""
        tool_schemas = self._build_tool_schemas()
        messages = self.memory.get_messages()

        with global_tracer.start_span("llm_generate", {"msg_count": len(messages)}):
            llm_t0 = asyncio.get_event_loop().time()
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
            except Exception as e:
                global_metrics.increment_counter("llm_errors", tags={"error": type(e).__name__})
                raise
        return response

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

        Returns both the record (for AgentRunResult) and the message (for memory).
        """
        with global_tracer.start_span("tool_execution", {"tool": parsed.name}) as span:
            t0 = time.monotonic()

            # Find tool
            tool = self._find_tool(parsed.name)

            if tool is None:
                return self._tool_error(
                    parsed, step_num, t0, span,
                    f"Tool '{parsed.name}' not found in agent's tool list",
                    "tool_not_found_errors",
                )

            if isinstance(tool, dict):
                return self._tool_error(
                    parsed, step_num, t0, span,
                    f"Tool '{parsed.name}' is a raw dict schema, not executable. "
                    "Wrap with MCPTool.from_mcp_client().",
                    "tool_not_executable_errors",
                )

            # Execute
            try:
                if self.verbose:
                    logger.info(f"[{self.name}] Executing {parsed.name}({parsed.arguments})")

                result: ToolResult = await tool.execute(**parsed.arguments)
                duration_ms = (time.monotonic() - t0) * 1000

                tool_msg = ToolExecutionResultMessage.from_tool_result(
                    tool_result=result,
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
                return record, tool_msg

            except Exception as e:
                return self._tool_error(
                    parsed, step_num, t0, span,
                    str(e), "tool_execution_errors",
                )

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
