"""Guardrail checking helpers for AssistantAgent."""

from __future__ import annotations


from ravi.kernel import ToolUseBlock
from ravi.reasoning.guardrails._contracts import (
    GuardrailContext,
    GuardrailResult,
    GuardrailType,
)
from ravi.reasoning.guardrails.runner import run_guardrails


async def check_input_guardrails(
    *,
    guardrails: list[object],
    agent_name: str,
    run_id: str,
    input_text: str,
) -> list[GuardrailResult]:
    """Run INPUT guardrails; raise GuardrailTripwireError on tripwire."""
    ctx = GuardrailContext(
        agent_name=agent_name,
        run_id=run_id,
        input_text=input_text,
    )
    return await run_guardrails(guardrails, ctx, guardrail_type=GuardrailType.INPUT)


async def check_output_guardrails(
    *,
    guardrails: list[object],
    agent_name: str,
    run_id: str,
    output_text: str | None,
) -> list[GuardrailResult]:
    """Run OUTPUT guardrails; raise GuardrailTripwireError on tripwire."""
    ctx = GuardrailContext(
        agent_name=agent_name,
        run_id=run_id,
        output_text=output_text,
    )
    return await run_guardrails(guardrails, ctx, guardrail_type=GuardrailType.OUTPUT)


async def check_tool_call_guardrails(
    *,
    guardrails: list[object],
    agent_name: str,
    run_id: str,
    tool_use: ToolUseBlock,
) -> list[GuardrailResult]:
    """Run TOOL_CALL guardrails; raise GuardrailTripwireError on tripwire."""
    ctx = GuardrailContext(
        agent_name=agent_name,
        run_id=run_id,
        tool_name=tool_use.tool_name,
        tool_arguments=dict(tool_use.arguments),
    )
    return await run_guardrails(guardrails, ctx, guardrail_type=GuardrailType.TOOL_CALL)
