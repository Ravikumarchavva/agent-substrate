"""Guardrail runner — executes guardrails in parallel."""
from __future__ import annotations

import asyncio

from ravi.exceptions import GuardrailTripwireError
from ravi.logger import setup_logging
from ravi.agents.guardrails._contracts import (
    GuardrailContext,
    GuardrailResult,
    GuardrailType,
)

logger = setup_logging()


async def run_guardrails(
    guardrails: list[object],
    ctx: GuardrailContext,
    *,
    guardrail_type: GuardrailType | None = None,
) -> list[GuardrailResult]:
    """Execute matching guardrails in parallel; raise on tripwire."""
    to_run = [
        g for g in guardrails
        if guardrail_type is None
        or getattr(g, "guardrail_type", None) == guardrail_type
    ]
    if not to_run:
        return []

    async def _safe_check(guardrail: object) -> GuardrailResult:
        name = getattr(guardrail, "name", repr(guardrail))
        gtype = getattr(guardrail, "guardrail_type", GuardrailType.INPUT)
        try:
            result: GuardrailResult = await guardrail.check(ctx)  # type: ignore[union-attr]
            if not result.passed:
                level = "error" if result.tripwire else "warning"
                logger.log(
                    40 if level == "error" else 30,
                    "[Guardrail:%s] %s: %s",
                    name,
                    "TRIPWIRE" if result.tripwire else "FAILED",
                    result.message,
                )
            return result
        except Exception as exc:
            logger.error("[Guardrail:%s] Unexpected error: %s", name, exc, exc_info=True)
            return GuardrailResult(
                guardrail_name=name,
                guardrail_type=gtype,
                passed=True,
                message="Guardrail encountered an internal error (failing open)",
                metadata={"error_type": type(exc).__name__},
            )

    results: list[GuardrailResult] = list(
        await asyncio.gather(*[_safe_check(g) for g in to_run])
    )

    for result in results:
        if result.tripwire and not result.passed:
            raise GuardrailTripwireError(
                message=f"Guardrail '{result.guardrail_name}' triggered tripwire: {result.message}",
                guardrail_name=result.guardrail_name,
                details={"guardrail_type": result.guardrail_type.value},
            )

    return results
