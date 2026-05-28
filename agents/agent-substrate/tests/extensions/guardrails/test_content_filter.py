"""Tests for ContentFilterGuardrail."""

from __future__ import annotations


from ravi.kernel.guardrails import (
    GuardrailContext,
    GuardrailType,
)
from ravi.reasoning.guardrails import (
    ContentFilterGuardrail,
)


# ══════════════════════════════════════════════════════════════════════════════
# Pass cases
# ══════════════════════════════════════════════════════════════════════════════


async def test_clean_text_passes():
    g = ContentFilterGuardrail(blocked_keywords=["bomb"])
    ctx = GuardrailContext(input_text="What's the weather?")
    result = await g.check(ctx)
    assert result.passed


async def test_empty_text_passes():
    g = ContentFilterGuardrail(blocked_keywords=["hack"])
    ctx = GuardrailContext(input_text="")
    result = await g.check(ctx)
    assert result.passed


# ══════════════════════════════════════════════════════════════════════════════
# Keyword blocking
# ══════════════════════════════════════════════════════════════════════════════


async def test_keyword_blocked():
    g = ContentFilterGuardrail(blocked_keywords=["bomb"])
    ctx = GuardrailContext(input_text="How to make a bomb at home?")
    result = await g.check(ctx)
    assert not result.passed


async def test_keyword_case_insensitive():
    g = ContentFilterGuardrail(blocked_keywords=["hack"])
    ctx = GuardrailContext(input_text="HACK the system now")
    result = await g.check(ctx)
    assert not result.passed


async def test_multiple_keywords_any_triggers():
    g = ContentFilterGuardrail(blocked_keywords=["kill", "bomb"])
    ctx = GuardrailContext(input_text="I want to kill the process and bomb it")
    result = await g.check(ctx)
    assert not result.passed


# ══════════════════════════════════════════════════════════════════════════════
# Pattern blocking
# ══════════════════════════════════════════════════════════════════════════════


async def test_regex_pattern_blocked():
    g = ContentFilterGuardrail(blocked_patterns=[r"kill\s+\w+"])
    ctx = GuardrailContext(input_text="I want to kill someone")
    result = await g.check(ctx)
    assert not result.passed


async def test_regex_no_match_passes():
    g = ContentFilterGuardrail(blocked_patterns=[r"\d{4}-\d{4}"])
    ctx = GuardrailContext(input_text="No numbers here.")
    result = await g.check(ctx)
    assert result.passed


# ══════════════════════════════════════════════════════════════════════════════
# Output variant
# ══════════════════════════════════════════════════════════════════════════════


async def test_output_filter_blocks_output_text():
    g = ContentFilterGuardrail(
        guardrail_type=GuardrailType.OUTPUT,
        blocked_keywords=["classified"],
    )
    ctx = GuardrailContext(output_text="This is classified information.")
    result = await g.check(ctx)
    assert not result.passed


async def test_output_filter_ignores_input_text():
    g = ContentFilterGuardrail(
        guardrail_type=GuardrailType.OUTPUT,
        blocked_keywords=["bomb"],
    )
    ctx = GuardrailContext(input_text="bomb in input", output_text="Safe response.")
    result = await g.check(ctx)
    assert result.passed


# ══════════════════════════════════════════════════════════════════════════════
# Tripwire flag
# ══════════════════════════════════════════════════════════════════════════════


async def test_tripwire_true_set_on_failure():
    g = ContentFilterGuardrail(blocked_keywords=["hack"], tripwire=True)
    ctx = GuardrailContext(input_text="hack the planet")
    result = await g.check(ctx)
    assert result.tripwire is True


async def test_tripwire_false_no_hard_stop():
    g = ContentFilterGuardrail(blocked_keywords=["hack"], tripwire=False)
    ctx = GuardrailContext(input_text="hack the planet")
    result = await g.check(ctx)
    assert not result.passed
    assert not result.tripwire
