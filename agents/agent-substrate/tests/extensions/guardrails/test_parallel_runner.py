"""Tests for run_guardrails — parallel execution, early-exit on tripwire."""

from __future__ import annotations


import pytest

from ravi.kernel.guardrails import (
    GuardrailContext,
    GuardrailType,
)
from ravi.extensions.guardrails import (
    ContentFilterGuardrail,
    PIIDetectionGuardrail,
    run_guardrails,
)
from ravi.exceptions import GuardrailTripwireError


# ══════════════════════════════════════════════════════════════════════════════
# Parallel pass
# ══════════════════════════════════════════════════════════════════════════════


async def test_all_pass_returns_all_results():
    guards = [
        ContentFilterGuardrail(blocked_keywords=["bomb"], tripwire=False),
        PIIDetectionGuardrail(tripwire=False),
    ]
    ctx = GuardrailContext(input_text="Hello, how are you?")
    results = await run_guardrails(guards, ctx, guardrail_type=GuardrailType.INPUT)
    assert len(results) == 2
    assert all(r.passed for r in results)


# ══════════════════════════════════════════════════════════════════════════════
# Tripwire raises
# ══════════════════════════════════════════════════════════════════════════════


async def test_tripwire_raises_guardrail_error():
    guards = [
        PIIDetectionGuardrail(tripwire=True),
    ]
    ctx = GuardrailContext(input_text="My email is bad@actor.com")
    with pytest.raises(GuardrailTripwireError):
        await run_guardrails(guards, ctx, guardrail_type=GuardrailType.INPUT)


# ══════════════════════════════════════════════════════════════════════════════
# Empty list
# ══════════════════════════════════════════════════════════════════════════════


async def test_empty_guardrail_list_returns_empty():
    ctx = GuardrailContext(input_text="Anything")
    results = await run_guardrails([], ctx, guardrail_type=GuardrailType.INPUT)
    assert results == []


# ══════════════════════════════════════════════════════════════════════════════
# Type filter: only matching guardrails run
# ══════════════════════════════════════════════════════════════════════════════


async def test_output_type_skips_input_guardrails():
    input_guard = ContentFilterGuardrail(
        guardrail_type=GuardrailType.INPUT,
        blocked_keywords=["secret"],
        tripwire=True,
    )
    # If the input guard fired on output context, it would fail — but it shouldn't.
    ctx = GuardrailContext(output_text="secret output")
    # run_guardrails filters by type, so passing OUTPUT type should skip INPUT guards
    results = await run_guardrails(
        [input_guard], ctx, guardrail_type=GuardrailType.OUTPUT
    )
    # Input guard is skipped → no results
    assert all(r.passed for r in results)
