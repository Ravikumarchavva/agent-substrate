"""Tests for PIIDetectionGuardrail."""

from __future__ import annotations

import pytest

from ravi.kernel.guardrails import (
    GuardrailContext,
    GuardrailType,
)
from ravi.reasoning.guardrails import (
    PIIDetectionGuardrail,
)


# ══════════════════════════════════════════════════════════════════════════════
# Pass / clean inputs
# ══════════════════════════════════════════════════════════════════════════════


async def test_clean_input_passes():
    g = PIIDetectionGuardrail(tripwire=False)
    ctx = GuardrailContext(input_text="What is the weather today?")
    result = await g.check(ctx)
    assert result.passed


async def test_empty_text_passes():
    g = PIIDetectionGuardrail()
    ctx = GuardrailContext(input_text="")
    result = await g.check(ctx)
    assert result.passed


# ══════════════════════════════════════════════════════════════════════════════
# Detection — each PII type
# ══════════════════════════════════════════════════════════════════════════════


async def test_email_detected():
    g = PIIDetectionGuardrail(tripwire=False)
    ctx = GuardrailContext(input_text="Contact me at foo@bar.com")
    result = await g.check(ctx)
    assert not result.passed
    assert "email" in result.metadata.get("detected_types", [])


async def test_ssn_detected():
    g = PIIDetectionGuardrail(tripwire=False)
    ctx = GuardrailContext(input_text="My SSN is 123-45-6789")
    result = await g.check(ctx)
    assert not result.passed
    assert "ssn" in result.metadata.get("detected_types", [])


async def test_phone_detected():
    g = PIIDetectionGuardrail(tripwire=False)
    ctx = GuardrailContext(input_text="Call me at 555-867-5309")
    result = await g.check(ctx)
    assert not result.passed
    assert "phone_us" in result.metadata.get("detected_types", [])


async def test_credit_card_detected():
    g = PIIDetectionGuardrail(tripwire=False)
    ctx = GuardrailContext(input_text="Card: 4111 1111 1111 1111")
    result = await g.check(ctx)
    assert not result.passed
    assert "credit_card" in result.metadata.get("detected_types", [])


# ══════════════════════════════════════════════════════════════════════════════
# Tripwire behaviour
# ══════════════════════════════════════════════════════════════════════════════


async def test_tripwire_true_sets_flag():
    g = PIIDetectionGuardrail(tripwire=True)
    ctx = GuardrailContext(input_text="Email: attacker@evil.com")
    result = await g.check(ctx)
    assert result.tripwire is True


async def test_tripwire_false_does_not_set_flag():
    g = PIIDetectionGuardrail(tripwire=False)
    ctx = GuardrailContext(input_text="Email: user@example.com")
    result = await g.check(ctx)
    assert not result.tripwire


# ══════════════════════════════════════════════════════════════════════════════
# Scoped PII types
# ══════════════════════════════════════════════════════════════════════════════


async def test_scoped_to_email_only_ignores_ssn():
    g = PIIDetectionGuardrail(pii_types=["email"], tripwire=False)
    ctx = GuardrailContext(input_text="My SSN is 123-45-6789")
    result = await g.check(ctx)
    # SSN not in scope, so should pass
    assert result.passed


async def test_scoped_to_email_catches_email():
    g = PIIDetectionGuardrail(pii_types=["email"], tripwire=False)
    ctx = GuardrailContext(input_text="Email me at x@y.com")
    result = await g.check(ctx)
    assert not result.passed


# ══════════════════════════════════════════════════════════════════════════════
# Output guardrail variant
# ══════════════════════════════════════════════════════════════════════════════


async def test_output_guardrail_inspects_output_text():
    g = PIIDetectionGuardrail(guardrail_type=GuardrailType.OUTPUT, tripwire=False)
    ctx = GuardrailContext(output_text="Your card 4111 1111 1111 1111 is saved")
    result = await g.check(ctx)
    assert not result.passed


async def test_output_guardrail_ignores_input_text():
    g = PIIDetectionGuardrail(guardrail_type=GuardrailType.OUTPUT, tripwire=False)
    ctx = GuardrailContext(input_text="SSN: 123-45-6789", output_text="All looks good.")
    result = await g.check(ctx)
    assert result.passed


# ══════════════════════════════════════════════════════════════════════════════
# Custom patterns
# ══════════════════════════════════════════════════════════════════════════════


async def test_custom_pattern_detected():
    g = PIIDetectionGuardrail(
        custom_patterns={"employee_id": r"EMP-\d{6}"},
        tripwire=False,
    )
    ctx = GuardrailContext(input_text="Submitted by EMP-042312")
    result = await g.check(ctx)
    assert not result.passed
    assert "employee_id" in result.metadata.get("detected_types", [])


async def test_invalid_custom_pattern_raises_value_error():
    with pytest.raises(ValueError, match="Invalid custom PII pattern"):
        PIIDetectionGuardrail(custom_patterns={"bad": "["})
