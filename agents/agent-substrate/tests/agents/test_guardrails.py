from __future__ import annotations

import pytest
from ravi.exceptions import GuardrailTripwireError
from ravi.agents.guardrails import (
    MaxTokenGuardrail,
    ContentFilterGuardrail,
    GuardrailType,
    run_guardrails,
)
from ravi.agents.guardrails._contracts import GuardrailContext


@pytest.mark.asyncio
async def test_max_token_guardrail():
    # Pass path
    gr = MaxTokenGuardrail(max_tokens=10, chars_per_token=4.0, tripwire=True)
    ctx = GuardrailContext(input_text="abc")  # token count 0 or 1
    res = await gr.check(ctx)
    assert res.passed is True

    # Fail path
    gr_fail = MaxTokenGuardrail(max_tokens=1, chars_per_token=1.0, tripwire=True)
    ctx_fail = GuardrailContext(input_text="a" * 100)  # guaranteed token count > 1
    res_fail = await gr_fail.check(ctx_fail)
    assert res_fail.passed is False
    assert res_fail.tripwire is True


@pytest.mark.asyncio
async def test_content_filter_guardrail():
    # Output guardrail
    gr = ContentFilterGuardrail(
        guardrail_type=GuardrailType.OUTPUT,
        blocked_keywords=["badword"],
        tripwire=True,
    )
    
    # Pass path
    ctx_pass = GuardrailContext(output_text="This is a clean response.")
    res_pass = await gr.check(ctx_pass)
    assert res_pass.passed is True

    # Fail path
    ctx_fail = GuardrailContext(output_text="The BADWORD is secret.")
    res_fail = await gr.check(ctx_fail)
    assert res_fail.passed is False
    assert res_fail.tripwire is True


@pytest.mark.asyncio
async def test_run_guardrails():
    gr = MaxTokenGuardrail(max_tokens=2, chars_per_token=1.0, tripwire=True)
    ctx = GuardrailContext(input_text="a" * 100)
    
    with pytest.raises(GuardrailTripwireError) as exc_info:
        await run_guardrails([gr], ctx, guardrail_type=GuardrailType.INPUT)
    assert exc_info.value.guardrail_name == "max_token"
