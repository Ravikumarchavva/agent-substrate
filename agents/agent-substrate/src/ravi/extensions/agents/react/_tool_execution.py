"""Tool parsing, lookup, approval, and execution helpers for ReActAgent.

Extracts the tool-execution concerns from react_agent.py into standalone
functions that the agent methods delegate to. Every function that touches
I/O is ``async def``; pure helpers are plain ``def``.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple
from uuid import uuid4

from opentelemetry.trace import Status, StatusCode

from ravi.kernel.agents.agent_result import ToolCallRecord
from ravi.kernel.execution.context import ExecutionContext
from ravi.kernel.hooks import HookEvent, HookManager
from ravi.kernel.messages.client_messages import (
    ToolCallMessage,
    ToolExecutionResultMessage,
)
from ravi.kernel.middleware.base import (
    MiddlewareContext,
    MiddlewareStage,
)
from ravi.extensions.resilience.policies import RetryPolicy, _calculate_delay
from ravi.kernel.runtime import AgentId, AgentRuntime
from ravi.kernel.tools.base_tool import HitlMode, ToolResult
from ravi.kernel.agent_catalog import AgentCatalogRegistry
from ravi.catalog.tools.human_input.tool import (
    ToolApprovalAction,
    ToolApprovalHandler,
    ToolApprovalRequest,
    ToolApprovalResponse,
)
from ravi.shared.observability import global_metrics, logger
from ravi.kernel.messages.content import TextBlock
from ravi.exceptions import GuardrailTripwireError


# ---------------------------------------------------------------------------
# Parsed tool-call (normalised from any SDK shape)
# ---------------------------------------------------------------------------


class ParsedToolCall:
    """Internal normalised representation of a tool call."""

    __slots__ = ("call_id", "name", "arguments")

    def __init__(self, call_id: str, name: str, arguments: Dict[str, Any]):
        self.call_id = call_id
        self.name = name
        self.arguments = arguments


# ---------------------------------------------------------------------------
# Tool execution context (replaces 22-parameter signature)
# ---------------------------------------------------------------------------


@dataclass
class ToolExecutionContext:
    """Agent-level execution environment for a tool call.

    Built once in ``_execute_tool()`` and passed down to both
    ``execute_tool_direct`` and ``execute_tool_via_runtime``.

    This replaces the 22-keyword-argument signature those functions used to
    carry, making them testable in isolation without constructing a full agent.
    """

    # Agent identity
    agent_name: str
    run_id: str

    # Execution policy
    tool_timeout: Optional[float]
    tool_retry_policy: RetryPolicy
    verbose: bool

    # Infrastructure
    hooks: HookManager
    middleware_pipeline: Any
    catalog: AgentCatalogRegistry
    tools: List[Any]

    # Optional components
    execution_context: Optional[ExecutionContext] = None
    tool_search_name: str = ""
    activate_tool_names_cb: Optional[Callable[[List[str]], None]] = None
    skill_manager: Optional[Any] = None
    runtime: Optional[AgentRuntime] = None
    agent_id: Optional[AgentId] = None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_tool_call(tc: Any) -> ParsedToolCall:
    """Normalise any tool-call shape into a ParsedToolCall.

    Handles: ToolCallMessage, OpenAI SDK objects with .function dict,
    raw dicts, and Pydantic ToolCall models.
    """
    call_id: Optional[str] = getattr(tc, "id", None)
    name: Optional[str] = None
    args: Any = None

    # 1. ToolCallMessage (our own type)
    if isinstance(tc, ToolCallMessage):
        return ParsedToolCall(
            call_id=tc.tool_call_id or str(uuid4()),
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

    return ParsedToolCall(
        call_id=call_id or str(uuid4()),
        name=name or "unknown",
        arguments=args if isinstance(args, dict) else {},
    )


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


def find_tool(
    name: str,
    catalog: AgentCatalogRegistry,
    tools: List[Any],
) -> Optional[Any]:
    """Look up a tool by name (or alias) from the catalog, then fallback to list."""
    catalog_tool = catalog.get_tool(name)
    if catalog_tool is not None:
        return catalog_tool
    for t in tools:
        t_name = getattr(t, "name", None) or (
            t.get("name") if isinstance(t, dict) else None
        )
        if t_name == name:
            return t
    return None


def content_to_str(content: Any) -> str:
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


# ---------------------------------------------------------------------------
# Error helper
# ---------------------------------------------------------------------------


def build_tool_error(
    parsed: ParsedToolCall,
    t0: float,
    span: Any,
    error_msg: str,
    metric_name: str,
    agent_name: str,
) -> Tuple[ToolCallRecord, ToolExecutionResultMessage]:
    """Build error record + message for a failed tool call."""
    duration_ms = (time.monotonic() - t0) * 1000
    logger.error(f"[{agent_name}] {error_msg}")
    span.set_status(Status(StatusCode.ERROR))
    global_metrics.increment_counter(metric_name, tags={"tool": parsed.name})

    tool_msg = ToolExecutionResultMessage(
        content=[TextBlock(text=json.dumps({"error": error_msg}))],
        tool_call_id=parsed.call_id,
        name=parsed.name,
        is_error=True,
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


# ---------------------------------------------------------------------------
# HITL approval gate
# ---------------------------------------------------------------------------


def tool_needs_approval(
    tool_name: str,
    tools_requiring_approval: Optional[List[str]],
) -> bool:
    """Check whether the given tool requires human approval."""
    if tools_requiring_approval is None:
        # Handler set but no explicit list → all tools need approval
        return True
    return tool_name in tools_requiring_approval


async def run_hitl_approval(
    parsed: ParsedToolCall,
    tool: Any,
    step_num: int,
    handler: ToolApprovalHandler,
    agent_name: str,
) -> Optional[Tuple[ToolCallRecord, ToolExecutionResultMessage]]:
    """Request human approval. Returns error tuple if denied, else None.

    On MODIFY, mutates ``parsed.arguments`` in place.
    """
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
        approval = await handler.request_approval(approval_request)
    except Exception as exc:
        logger.error(f"[{agent_name}] Approval handler error: {exc}")
        approval = ToolApprovalResponse(
            request_id=approval_request.request_id,
            action=ToolApprovalAction.DENY,
            reason=f"Approval handler error: {exc}",
        )

    if approval.action == ToolApprovalAction.DENY:
        deny_msg = approval.reason or "User denied tool execution"
        logger.info(f"[{agent_name}] Tool '{parsed.name}' DENIED: {deny_msg}")
        return None  # caller must build error via build_tool_error
        # We return None with a convention: caller checks approval.action

    if approval.action == ToolApprovalAction.MODIFY:
        if approval.modified_arguments:
            logger.info(
                f"[{agent_name}] Tool '{parsed.name}' MODIFIED: "
                f"{parsed.arguments} → {approval.modified_arguments}"
            )
            parsed.arguments = approval.modified_arguments
        else:
            logger.info(
                f"[{agent_name}] Tool '{parsed.name}' APPROVED (modify with no changes)"
            )
    else:
        logger.info(f"[{agent_name}] Tool '{parsed.name}' APPROVED")

    return None  # pragma: no cover — signal "proceed"


async def request_tool_approval(
    parsed: ParsedToolCall,
    tool: Any,
    step_num: int,
    t0: float,
    span: Any,
    handler: ToolApprovalHandler,
    hooks: HookManager,
    agent_name: str,
) -> Optional[Tuple[ToolCallRecord, ToolExecutionResultMessage]]:
    """Full HITL approval gate.  Returns error tuple if denied, else None (proceed)."""
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
        approval = await handler.request_approval(approval_request)
    except Exception as exc:
        logger.error(f"[{agent_name}] Approval handler error: {exc}")
        approval = ToolApprovalResponse(
            request_id=approval_request.request_id,
            action=ToolApprovalAction.DENY,
            reason=f"Approval handler error: {exc}",
        )

    if approval.action == ToolApprovalAction.DENY:
        deny_msg = approval.reason or "User denied tool execution"
        logger.info(f"[{agent_name}] Tool '{parsed.name}' DENIED: {deny_msg}")
        result = build_tool_error(
            parsed,
            t0,
            span,
            f"Tool denied by user: {deny_msg}",
            "tool_denied_by_user",
            agent_name,
        )
        await hooks.dispatch(
            HookEvent.TOOL_END,
            {
                "event": "on_tool_end",
                "agent_name": agent_name,
                "tool_name": parsed.name,
                "is_error": True,
                "error": "denied_by_user",
                "reason": deny_msg,
                "duration_ms": (time.monotonic() - t0) * 1000,
            },
        )
        return result

    if approval.action == ToolApprovalAction.MODIFY:
        if approval.modified_arguments:
            logger.info(
                f"[{agent_name}] Tool '{parsed.name}' MODIFIED: "
                f"{parsed.arguments} → {approval.modified_arguments}"
            )
            parsed.arguments = approval.modified_arguments
        else:
            logger.info(
                f"[{agent_name}] Tool '{parsed.name}' APPROVED (modify with no changes)"
            )
    else:
        logger.info(f"[{agent_name}] Tool '{parsed.name}' APPROVED")

    return None  # proceed with execution


# ---------------------------------------------------------------------------
# Core tool execution (retry + timeout + middleware)
# ---------------------------------------------------------------------------


async def execute_tool_direct(
    parsed: ParsedToolCall,
    step_num: int,
    t0: float,
    span: Any,
    tool: Any,
    ctx: ToolExecutionContext,
) -> Tuple[ToolCallRecord, ToolExecutionResultMessage]:
    """Execute a tool with retry, timeout, middleware, and hooks.

    Returns the record + message tuple on success, or an error tuple on failure.
    All agent-level configuration is carried by ``ctx``.
    """
    agent_name = ctx.agent_name
    run_id = ctx.run_id
    verbose = ctx.verbose
    tool_timeout = ctx.tool_timeout
    tool_retry_policy = ctx.tool_retry_policy
    hooks = ctx.hooks
    middleware_pipeline = ctx.middleware_pipeline
    execution_context = ctx.execution_context
    tool_search_name = ctx.tool_search_name
    activate_tool_names_cb = ctx.activate_tool_names_cb
    skill_manager = ctx.skill_manager
    runtime = ctx.runtime
    catalog = ctx.catalog
    tools = ctx.tools

    last_error: Optional[Exception] = None
    for attempt in range(tool_retry_policy.max_retries + 1):
        try:
            if verbose:
                logger.info(
                    f"[{agent_name}] Executing {parsed.name}({parsed.arguments})"
                )

            # Build the actual execution coroutine
            async def _run_tool() -> ToolResult:
                lock_handle = None
                if (
                    runtime
                    and hasattr(runtime, "resource_locks")
                    and getattr(tool, "resource_uri", None)
                ):
                    from ravi.kernel.tools.base_tool import ToolRisk
                    from ravi.kernel.runtime._resource_lock import LockMode

                    mode = LockMode.EXCLUSIVE
                    if getattr(tool, "risk", None) == ToolRisk.SAFE:
                        mode = LockMode.SHARED
                    elif getattr(tool, "annotations", None) and getattr(
                        tool.annotations, "readOnlyHint", None
                    ):
                        mode = LockMode.SHARED

                    try:
                        uri = tool.resource_uri.format(**parsed.arguments)
                    except Exception:
                        uri = tool.resource_uri

                    lock_handle = await runtime.resource_locks.acquire(
                        resource_uri=uri,
                        agent_id=agent_name,
                        mode=mode,
                    )

                try:
                    # Saga coordination for critical tools
                    if (
                        runtime
                        and hasattr(runtime, "saga_coordinator")
                        and getattr(tool, "is_critical", False)
                    ):
                        saga_coordinator = runtime.saga_coordinator
                        req_hash = saga_coordinator.hash_request(
                            parsed.name, parsed.arguments
                        )

                        # Define compensating action
                        compensate_fn = None
                        if getattr(tool, "compensating_tool", None):

                            async def do_compensate(step_result: Any) -> None:
                                comp_tool = find_tool(
                                    tool.compensating_tool,
                                    catalog
                                    or (
                                        skill_manager._catalog
                                        if (
                                            skill_manager
                                            and hasattr(skill_manager, "_catalog")
                                        )
                                        else None
                                    ),
                                    tools or [],
                                )
                                if comp_tool:
                                    comp_args = dict(parsed.arguments)
                                    if isinstance(step_result, dict):
                                        for k, v in step_result.items():
                                            comp_args[f"step_result_{k}"] = v
                                        comp_args["step_result"] = step_result
                                    await comp_tool.execute(**comp_args)
                                else:
                                    logger.error(
                                        f"Compensating tool '{tool.compensating_tool}' not found for tool '{parsed.name}'"
                                    )

                            compensate_fn = do_compensate

                        async def do_action() -> Any:
                            if tool_timeout:
                                res = await asyncio.wait_for(
                                    tool.execute(**parsed.arguments),
                                    timeout=tool_timeout,
                                )
                            else:
                                res = await tool.execute(**parsed.arguments)

                            if hasattr(res, "model_dump"):
                                return res.model_dump(mode="json")
                            return {"result": str(res)}

                        # Run the step in the saga
                        async with saga_coordinator.begin(
                            run_id, agent_id=agent_name
                        ) as saga_ctx:
                            saga_res = await saga_ctx.step(
                                step_id=parsed.call_id,
                                action=do_action,
                                compensate=compensate_fn,
                                request_hash=req_hash,
                            )

                        # Reconstruct ToolResult from saga result
                        if isinstance(saga_res, dict) and "content" in saga_res:
                            return ToolResult.model_validate(saga_res)
                        else:
                            return ToolResult(content=[TextBlock(text=str(saga_res))])
                    else:
                        # Standard execution
                        if tool_timeout:
                            return await asyncio.wait_for(
                                tool.execute(**parsed.arguments),
                                timeout=tool_timeout,
                            )
                        return await tool.execute(**parsed.arguments)
                finally:
                    if lock_handle and runtime and hasattr(runtime, "resource_locks"):
                        await runtime.resource_locks.release(lock_handle)

            # Wrap with middleware pipeline when middleware is configured
            if middleware_pipeline.middleware:
                mw_ctx = MiddlewareContext(
                    stage=MiddlewareStage.TOOL_EXECUTION,
                    agent_name=agent_name,
                    run_id=run_id,
                    correlation_id=run_id,
                    input_text="",
                    tool_name=parsed.name,
                    tool_args=parsed.arguments,
                    metadata=(
                        execution_context.inherited_metadata()
                        if execution_context is not None
                        else {}
                    ),
                    parent_context=execution_context,
                )

                async def _do_tool(ctx: MiddlewareContext) -> ToolResult:
                    return await _run_tool()

                exec_result: ToolResult = await middleware_pipeline.run(
                    mw_ctx, _do_tool
                )
            else:
                exec_result = await _run_tool()

            activate_tool_names_cb([parsed.name])
            if parsed.name == tool_search_name and exec_result.app_data:
                matched_tool_names = exec_result.app_data.get("matched_tool_names", [])
                if isinstance(matched_tool_names, list):
                    activate_tool_names_cb(
                        [name for name in matched_tool_names if isinstance(name, str)]
                    )
                # Activate discovered skills so they enrich the prompt
                matched_skill_names = exec_result.app_data.get(
                    "matched_skill_names", []
                )
                if isinstance(matched_skill_names, list):
                    for skill_name in matched_skill_names:
                        if isinstance(skill_name, str):
                            if catalog is not None:
                                catalog.activate_skill(skill_name)
                            elif skill_manager is not None:
                                skill_manager.activate(skill_name)

            duration_ms = (time.monotonic() - t0) * 1000

            tool_msg = ToolExecutionResultMessage.from_tool_result(
                tool_result=exec_result,
                tool_call_id=parsed.call_id,
                tool_name=parsed.name,
            )
            global_metrics.increment_counter(
                "tool_executions",
                tags={"tool": parsed.name, "status": "success"},
            )

            record = ToolCallRecord(
                tool_name=parsed.name,
                call_id=parsed.call_id,
                arguments=parsed.arguments,
                result=content_to_str(tool_msg.content),
                is_error=False,
                duration_ms=duration_ms,
            )

            # ── LIFECYCLE HOOK: TOOL_END ─────────────────────
            await hooks.dispatch(
                HookEvent.TOOL_END,
                {
                    "event": "on_tool_end",
                    "agent_name": agent_name,
                    "tool_name": parsed.name,
                    "is_error": False,
                    "duration_ms": duration_ms,
                    "step": step_num,
                },
            )

            return record, tool_msg

        except asyncio.TimeoutError:
            last_error = TimeoutError(
                f"Tool '{parsed.name}' timed out after {tool_timeout}s"
            )
            if attempt < tool_retry_policy.max_retries:
                delay = _calculate_delay(attempt, tool_retry_policy)
                logger.warning(
                    f"[{agent_name}] Tool timeout, retry "
                    f"{attempt + 1}/{tool_retry_policy.max_retries} "
                    f"(waiting {delay:.1f}s)"
                )
                await asyncio.sleep(delay)
                continue

        except tool_retry_policy.retryable_exceptions as e:
            last_error = e
            if attempt < tool_retry_policy.max_retries:
                delay = _calculate_delay(attempt, tool_retry_policy)
                logger.warning(
                    f"[{agent_name}] Tool retry {attempt + 1}/"
                    f"{tool_retry_policy.max_retries}: {e} "
                    f"(waiting {delay:.1f}s)"
                )
                await asyncio.sleep(delay)
                continue

        except GuardrailTripwireError as e:
            duration_ms = (time.monotonic() - t0) * 1000
            from ravi.extensions.agents.react._guardrail_runner import (
                build_tool_blocked_message,
                build_tool_blocked_record,
            )

            tool_msg = build_tool_blocked_message(parsed, e.message)
            record = build_tool_blocked_record(parsed, e.message)
            record.duration_ms = duration_ms
            await hooks.dispatch(
                HookEvent.TOOL_END,
                {
                    "event": "on_tool_end",
                    "agent_name": agent_name,
                    "tool_name": parsed.name,
                    "is_error": True,
                    "error": f"Blocked by guardrail: {e.message}",
                    "duration_ms": duration_ms,
                    "step": step_num,
                },
            )
            return record, tool_msg

        except Exception as e:
            last_error = e
            break  # Non-retryable — do not keep trying

    # All retries exhausted
    error_msg = str(last_error) if last_error else "Unknown tool error"
    result = build_tool_error(
        parsed, t0, span, error_msg, "tool_execution_errors", agent_name
    )
    await hooks.dispatch(
        HookEvent.TOOL_END,
        {
            "event": "on_tool_end",
            "agent_name": agent_name,
            "tool_name": parsed.name,
            "is_error": True,
            "error": error_msg,
            "duration_ms": (time.monotonic() - t0) * 1000,
        },
    )
    return result


# ---------------------------------------------------------------------------
# Runtime-dispatched tool execution
# ---------------------------------------------------------------------------


async def execute_tool_via_runtime(
    parsed: ParsedToolCall,
    step_num: int,
    t0: float,
    span: Any,
    ctx: ToolExecutionContext,
) -> Tuple[ToolCallRecord, ToolExecutionResultMessage]:
    """Dispatch tool execution through the agent runtime."""
    assert ctx.runtime is not None
    assert ctx.agent_id is not None
    runtime = ctx.runtime
    agent_id = ctx.agent_id
    agent_name = ctx.agent_name
    hooks = ctx.hooks
    catalog = ctx.catalog
    tools = ctx.tools
    activate_tool_names_cb = ctx.activate_tool_names_cb

    payload = {
        "tool_name": parsed.name,
        "arguments": parsed.arguments,
        "call_id": parsed.call_id,
    }

    # Check if the tool declares a custom agent_id for routing
    tool = find_tool(parsed.name, catalog, tools)
    target_agent_id = getattr(tool, "agent_id", None)
    if target_agent_id is None:
        target_agent_id = AgentId("tool_executor", agent_id.key)

    try:
        response = await runtime.send_message(
            payload,
            sender=agent_id,
            recipient=target_agent_id,
        )
    except Exception as exc:
        result = build_tool_error(
            parsed,
            t0,
            span,
            f"Runtime dispatch failed: {exc}",
            "tool_runtime_errors",
            agent_name,
        )
        await hooks.dispatch(
            HookEvent.TOOL_END,
            {
                "event": "on_tool_end",
                "agent_name": agent_name,
                "tool_name": parsed.name,
                "is_error": True,
                "error": str(exc),
                "duration_ms": (time.monotonic() - t0) * 1000,
            },
        )
        return result

    # Parse response dict from ToolExecutorHandler
    assert isinstance(response, dict)
    duration_ms = (time.monotonic() - t0) * 1000
    is_error = response.get("is_error", False)

    content = response.get("content", [])
    tool_msg = ToolExecutionResultMessage(
        content=content,
        tool_call_id=parsed.call_id,
        name=parsed.name,
        is_error=is_error,
        media=response.get("media"),
    )
    if response.get("app_data"):
        tool_msg.app_data = response["app_data"]

    record = ToolCallRecord(
        tool_name=parsed.name,
        call_id=parsed.call_id,
        arguments=parsed.arguments,
        result=content_to_str(content),
        is_error=is_error,
        duration_ms=duration_ms,
    )

    activate_tool_names_cb([parsed.name])

    await hooks.dispatch(
        HookEvent.TOOL_END,
        {
            "event": "on_tool_end",
            "agent_name": agent_name,
            "tool_name": parsed.name,
            "is_error": is_error,
            "duration_ms": duration_ms,
            "step": step_num,
        },
    )

    return record, tool_msg
