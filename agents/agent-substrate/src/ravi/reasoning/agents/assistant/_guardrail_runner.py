"""Guardrail checking helpers for ReActAgent.

Centralises the input / output / tool-call guardrail invocations
so the main agent module only needs thin delegation calls.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import List, Optional

from ravi.fabric.agents_base.agent_result import (
    AgentRunResult,
    AggregatedUsage,
    RunStatus,
    StepResult,
    ToolCallRecord,
)
from ravi.kernel.tools.parsing import ParsedToolCall
from ravi.exceptions import GuardrailTripwireError
from ravi.kernel.guardrails.base_guardrail import (
    BaseGuardrail,
    GuardrailContext,
    GuardrailResult,
    GuardrailType,
)
from ravi.reasoning.guardrails.runner import run_guardrails
from ravi.kernel.messages.client_messages import (
    AssistantMessage,
    ToolExecutionResultMessage,
)
from ravi.kernel.messages.content import TextBlock


# ---------------------------------------------------------------------------
# Input guardrails
# ---------------------------------------------------------------------------


async def check_input_guardrails(
    *,
    guardrails: List[BaseGuardrail],
    agent_name: str,
    run_id: str,
    input_text: str,
) -> List[GuardrailResult]:
    """Run input guardrails.  Returns results list.  Raises on tripwire."""
    ctx = GuardrailContext(
        agent_name=agent_name,
        run_id=run_id,
        input_text=input_text,
    )
    return await run_guardrails(
        guardrails,
        ctx,
        guardrail_type=GuardrailType.INPUT,
    )


def build_guardrail_tripped_result(
    *,
    error: GuardrailTripwireError,
    run_id: str,
    agent_name: str,
    run_start: datetime,
    steps: List[StepResult],
    usage: AggregatedUsage,
    max_iterations: int,
    guardrail_results: List[GuardrailResult],
    output_prefix: str = "Request blocked",
) -> AgentRunResult:
    """Build an ``AgentRunResult`` for a guardrail tripwire."""
    run_end = datetime.now(timezone.utc)
    extra = [error.details["result"]] if "result" in error.details else []
    return AgentRunResult(
        run_id=run_id,
        agent_name=agent_name,
        output=[f"{output_prefix}: {error.message}"],
        status=RunStatus.GUARDRAIL_TRIPPED,
        steps=steps,
        usage=usage,
        start_time=run_start,
        end_time=run_end,
        duration_seconds=(run_end - run_start).total_seconds(),
        max_iterations=max_iterations,
        error=error.message,
        guardrail_results=guardrail_results + extra,
    )


# ---------------------------------------------------------------------------
# Output guardrails
# ---------------------------------------------------------------------------


async def check_output_guardrails(
    *,
    guardrails: List[BaseGuardrail],
    agent_name: str,
    run_id: str,
    output_text: Optional[str],
    raw_message: AssistantMessage,
) -> List[GuardrailResult]:
    """Run output guardrails.  Returns results list.  Raises on tripwire."""
    ctx = GuardrailContext(
        agent_name=agent_name,
        run_id=run_id,
        output_text=output_text,
        raw_message=raw_message,
    )
    return await run_guardrails(
        guardrails,
        ctx,
        guardrail_type=GuardrailType.OUTPUT,
    )


# ---------------------------------------------------------------------------
# Tool-call guardrails
# ---------------------------------------------------------------------------


async def check_tool_call_guardrails(
    *,
    input_guardrails: List[BaseGuardrail],
    output_guardrails: List[BaseGuardrail],
    agent_name: str,
    run_id: str,
    parsed: ParsedToolCall,
) -> List[GuardrailResult]:
    """Run TOOL_CALL guardrails from both input and output lists.

    Returns accumulated results list.  Raises ``GuardrailTripwireError``
    if any guardrail trips.
    """
    all_guardrails = input_guardrails + output_guardrails
    tool_guardrails = [
        g for g in all_guardrails if g.guardrail_type == GuardrailType.TOOL_CALL
    ]
    if not tool_guardrails:
        return []
    ctx = GuardrailContext(
        agent_name=agent_name,
        run_id=run_id,
        tool_name=parsed.name,
        tool_arguments=parsed.arguments,
    )
    return await run_guardrails(
        tool_guardrails,
        ctx,
        guardrail_type=GuardrailType.TOOL_CALL,
    )


def build_tool_blocked_message(
    parsed: ParsedToolCall,
    error_message: str,
) -> ToolExecutionResultMessage:
    """Build a ToolExecutionResultMessage for a guardrail-blocked tool call."""
    return ToolExecutionResultMessage(
        content=[
            TextBlock(text=json.dumps({"error": f"Tool blocked: {error_message}"}))
        ],
        tool_call_id=parsed.call_id,
        name=parsed.name,
        is_error=True,
    )


def build_tool_blocked_record(
    parsed: ParsedToolCall,
    error_message: str,
) -> ToolCallRecord:
    """Build a ToolCallRecord for a guardrail-blocked tool call."""
    return ToolCallRecord(
        tool_name=parsed.name,
        call_id=parsed.call_id,
        arguments=parsed.arguments,
        result=f"Blocked by guardrail: {error_message}",
        is_error=True,
    )
