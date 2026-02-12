"""Guardrail runner — executes guardrails in parallel with observability.

The runner is the single entry point the agent uses to fire guardrails.
It handles:
  - Filtering guardrails by type (input / output / tool_call)
  - Parallel execution via asyncio.gather
  - OpenTelemetry spans and counters
  - Collecting results into an ordered list
  - Raising GuardrailTripwireError on hard stops
"""
from __future__ import annotations

import asyncio
from typing import List, Optional

from agent_framework.exceptions import GuardrailTripwireError
from agent_framework.guardrails.base_guardrail import (
    BaseGuardrail,
    GuardrailContext,
    GuardrailResult,
    GuardrailType,
)
from agent_framework.observability import global_metrics, global_tracer, logger


async def run_guardrails(
    guardrails: List[BaseGuardrail],
    ctx: GuardrailContext,
    *,
    guardrail_type: Optional[GuardrailType] = None,
) -> List[GuardrailResult]:
    """Execute all matching guardrails in parallel and return results.

    Args:
        guardrails: Full list of guardrails registered on the agent.
        ctx: Snapshot context for this check.
        guardrail_type: If set, only run guardrails of this type.
                        If None, run all.

    Returns:
        List of GuardrailResult (one per guardrail that ran).

    Raises:
        GuardrailTripwireError: If any guardrail returns tripwire=True.
    """
    # Filter
    to_run = [
        g for g in guardrails
        if guardrail_type is None or g.guardrail_type == guardrail_type
    ]

    if not to_run:
        return []

    # Run in parallel
    async def _safe_check(guardrail: BaseGuardrail) -> GuardrailResult:
        """Run a single guardrail with error handling and observability."""
        span_name = f"guardrail.{guardrail.name}"
        attrs = {
            "guardrail.name": guardrail.name,
            "guardrail.type": guardrail.guardrail_type.value,
            "agent.name": ctx.agent_name,
            "run_id": ctx.run_id,
        }
        with global_tracer.start_span(span_name, attrs):
            try:
                result = await guardrail.check(ctx)

                # Metrics
                if result.passed:
                    global_metrics.increment_counter(
                        "guardrail.passed",
                        tags={"name": guardrail.name, "type": guardrail.guardrail_type.value},
                    )
                else:
                    counter = "guardrail.tripped" if result.tripwire else "guardrail.failed"
                    global_metrics.increment_counter(
                        counter,
                        tags={"name": guardrail.name, "type": guardrail.guardrail_type.value},
                    )

                # Logging
                if not result.passed:
                    level = "warning" if not result.tripwire else "error"
                    msg = (
                        f"[Guardrail:{guardrail.name}] "
                        f"{'TRIPWIRE' if result.tripwire else 'FAILED'}: "
                        f"{result.message}"
                    )
                    if level == "error":
                        logger.error(msg)
                    else:
                        logger.warning(msg)

                return result

            except Exception as e:
                # Guardrails should not crash the agent.
                # Log full error server-side but return sanitized message.
                logger.error(
                    f"[Guardrail:{guardrail.name}] Unexpected error: {e}",
                    exc_info=True,
                )
                global_metrics.increment_counter(
                    "guardrail.errors",
                    tags={"name": guardrail.name, "type": guardrail.guardrail_type.value},
                )
                return GuardrailResult(
                    guardrail_name=guardrail.name,
                    guardrail_type=guardrail.guardrail_type,
                    passed=True,  # fail open
                    message="Guardrail encountered an internal error (failing open)",
                    metadata={"error_type": type(e).__name__},
                )

    results: List[GuardrailResult] = await asyncio.gather(
        *[_safe_check(g) for g in to_run]
    )

    # Check for tripwires (first one wins)
    for result in results:
        if result.tripwire and not result.passed:
            raise GuardrailTripwireError(
                message=f"Guardrail '{result.guardrail_name}' triggered tripwire: {result.message}",
                guardrail_name=result.guardrail_name,
                details={"result": result.model_dump()},
            )

    return results
