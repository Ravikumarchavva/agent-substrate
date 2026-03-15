"""Unit tests for the structured outputs system.

All tests use synchronous wrappers (asyncio.run) so the suite requires
only core pytest with no asyncio plugin.  Higher-level integration tests
(against live OpenAI) live in examples/10_structured_outputs.ipynb.
"""
from __future__ import annotations

import asyncio
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from agent_framework.core.structured.result import (
    StructuredOutputError,
    StructuredOutputResult,
)
from agent_framework.core.structured.schemas import (
    ClassificationResult,
    ContentSafetyJudge,
    ExtractionResult,
    RelevanceJudge,
)
from agent_framework.core.structured.parse import parse
from agent_framework.core.structured.judge import LLMJudge
from agent_framework.core.structured.router import StructuredRouter
from agent_framework.core.guardrails.base_guardrail import (
    GuardrailContext,
    GuardrailType,
)
from agent_framework.core.messages.client_messages import (
    SystemMessage,
    UserMessage,
)


# ---------------------------------------------------------------------------
# Helper: build a mock client that returns a pre-canned structured result
# ---------------------------------------------------------------------------

def _mock_client(result: StructuredOutputResult) -> MagicMock:
    client = MagicMock()
    client.generate_structured = AsyncMock(return_value=result)
    return client


def _make_user_messages(text: str = "Hello") -> list:
    return [UserMessage(content=[{"type": "text", "text": text}])]


# ---------------------------------------------------------------------------
# StructuredOutputResult
# ---------------------------------------------------------------------------

class TestStructuredOutputResult:
    def test_ok_when_parsed(self):
        r = StructuredOutputResult(parsed=ContentSafetyJudge(safe=True, reasoning="ok", violated_categories=[]))
        assert r.ok is True
        assert r.refused is False

    def test_refused_when_refusal_present(self):
        r = StructuredOutputResult(parsed=None, refusal="I cannot help with that.")
        assert r.ok is False
        assert r.refused is True

    def test_unwrap_returns_parsed(self):
        schema = ContentSafetyJudge(safe=True, reasoning="ok", violated_categories=[])
        r = StructuredOutputResult(parsed=schema)
        assert r.unwrap() is schema

    def test_unwrap_raises_on_none(self):
        r = StructuredOutputResult(parsed=None, refusal="refused")
        with pytest.raises(StructuredOutputError):
            r.unwrap()


# ---------------------------------------------------------------------------
# Built-in schemas
# ---------------------------------------------------------------------------

class TestSchemas:
    def test_content_safety_judge_fields(self):
        j = ContentSafetyJudge(safe=True, reasoning="looks fine", violated_categories=[])
        assert j.safe is True
        assert j.violated_categories == []

    def test_relevance_judge_score_bounds(self):
        j = RelevanceJudge(relevant=True, score=0.95, reasoning="on-topic")
        assert 0.0 <= j.score <= 1.0

    def test_classification_result(self):
        c = ClassificationResult(label="positive", confidence=0.9, reasoning="happy tone")
        assert c.label == "positive"

    def test_extraction_result_generic(self):
        class Address(BaseModel):
            street: str
            city: str

        addr = Address(street="123 Main St", city="Springfield")
        e = ExtractionResult[Address](data=addr, extraction_notes="extracted cleanly")
        assert e.data.city == "Springfield"


# ---------------------------------------------------------------------------
# parse()
# ---------------------------------------------------------------------------

class TestParseUtility:
    def test_parse_calls_generate_structured(self):
        """parse() must forward messages + schema to client.generate_structured."""
        parsed_val = ContentSafetyJudge(safe=True, reasoning="ok", violated_categories=[])
        fake_result = StructuredOutputResult(parsed=parsed_val)
        client = _mock_client(fake_result)
        messages = _make_user_messages("is this safe?")

        result = asyncio.run(parse(client, messages, ContentSafetyJudge))

        client.generate_structured.assert_called_once()
        call_args = client.generate_structured.call_args
        # First positional arg is messages, second is schema
        assert call_args[0][1] is ContentSafetyJudge
        assert result.ok is True

    def test_parse_prepends_system_message(self):
        """When ``system=`` is supplied, a SystemMessage must appear first."""
        fake_result = StructuredOutputResult(
            parsed=ContentSafetyJudge(safe=True, reasoning="ok", violated_categories=[])
        )
        client = _mock_client(fake_result)
        messages = _make_user_messages("test")

        asyncio.run(parse(client, messages, ContentSafetyJudge, system="You are a judge."))

        sent_messages = client.generate_structured.call_args[0][0]
        assert isinstance(sent_messages[0], SystemMessage)
        assert "judge" in sent_messages[0].content.lower()

    def test_parse_returns_refusal_result(self):
        fake_result = StructuredOutputResult(parsed=None, refusal="I cannot help.")
        client = _mock_client(fake_result)

        result = asyncio.run(parse(client, _make_user_messages(), ContentSafetyJudge))

        assert result.refused is True
        assert result.parsed is None


# ---------------------------------------------------------------------------
# LLMJudge
# ---------------------------------------------------------------------------

class TestLLMJudge:
    def _make_judge(self, client, **kwargs):
        return LLMJudge(
            client=client,
            schema=ContentSafetyJudge,
            system_prompt="You are a safety evaluator.",
            pass_field="safe",
            **kwargs,
        )

    def _output_ctx(self, text: str) -> GuardrailContext:
        return GuardrailContext(agent_name="test", output_text=text)

    def test_pass_when_safe(self):
        parsed = ContentSafetyJudge(safe=True, reasoning="clean", violated_categories=[])
        client = _mock_client(StructuredOutputResult(parsed=parsed))
        judge = self._make_judge(client)

        result = asyncio.run(judge.check(self._output_ctx("Nice weather today.")))

        assert result.passed is True
        assert result.tripwire is False

    def test_fail_when_unsafe(self):
        parsed = ContentSafetyJudge(
            safe=False, reasoning="harmful", violated_categories=["violence"]
        )
        client = _mock_client(StructuredOutputResult(parsed=parsed))
        judge = self._make_judge(client)

        result = asyncio.run(judge.check(self._output_ctx("Some bad content")))

        assert result.passed is False
        assert result.tripwire is False  # default tripwire_on_fail=False

    def test_tripwire_when_configured(self):
        parsed = ContentSafetyJudge(
            safe=False, reasoning="harmful", violated_categories=["violence"]
        )
        client = _mock_client(StructuredOutputResult(parsed=parsed))
        judge = self._make_judge(client, tripwire_on_fail=True)

        result = asyncio.run(judge.check(self._output_ctx("bad content")))

        assert result.tripwire is True

    def test_refusal_causes_fail(self):
        client = _mock_client(StructuredOutputResult(parsed=None, refusal="No."))
        judge = self._make_judge(client)

        result = asyncio.run(judge.check(self._output_ctx("something")))

        assert result.passed is False
        assert result.tripwire is True  # default tripwire_on_refusal=True

    def test_empty_text_passes_through(self):
        """No text to judge → pass without calling LLM."""
        client = _mock_client(StructuredOutputResult(parsed=None))
        judge = self._make_judge(client)

        ctx = GuardrailContext(agent_name="test", output_text=None)
        result = asyncio.run(judge.check(ctx))

        assert result.passed is True
        client.generate_structured.assert_not_called()

    def test_api_error_surfaces_as_failed_result(self):
        """Exceptions from generate_structured must not propagate — return failed result."""
        client = MagicMock()
        client.generate_structured = AsyncMock(side_effect=RuntimeError("network error"))
        judge = self._make_judge(client)

        result = asyncio.run(judge.check(self._output_ctx("some text")))

        assert result.passed is False
        assert "error" in result.message.lower() or "error" in str(result.metadata).lower()

    def test_input_guardrail_reads_input_text(self):
        """INPUT-type judge must read ctx.input_text, not ctx.output_text."""
        parsed = ContentSafetyJudge(safe=True, reasoning="clean", violated_categories=[])
        client = _mock_client(StructuredOutputResult(parsed=parsed))
        judge = LLMJudge(
            client=client,
            schema=ContentSafetyJudge,
            system_prompt="Check input safety.",
            guardrail_type=GuardrailType.INPUT,
            pass_field="safe",
        )
        ctx = GuardrailContext(agent_name="test", input_text="hello", output_text="ignored")
        asyncio.run(judge.check(ctx))

        sent_messages = client.generate_structured.call_args[0][0]
        # The user message text should be the input_text
        user_content = sent_messages[-1].content
        if isinstance(user_content, list) and user_content:
            first = user_content[0]
            text = first["text"] if isinstance(first, dict) else str(first)
        else:
            text = str(user_content)
        assert text == "hello"


# ---------------------------------------------------------------------------
# StructuredRouter
# ---------------------------------------------------------------------------

class TestStructuredRouter:
    class _Category(BaseModel):
        category: str
        reasoning: str

    def _make_router(self, category: str, routes: dict, **kwargs) -> tuple:
        parsed = self._Category(category=category, reasoning="clear case")
        client = _mock_client(StructuredOutputResult(parsed=parsed))
        router = StructuredRouter(
            client=client,
            routing_schema=self._Category,
            routing_key="category",
            routes=routes,
            system_prompt="Classify: furnish, repair, or photo.",
            **kwargs,
        )
        return router, client

    def test_dispatches_to_matching_callable(self):
        called_with = {}

        async def furnish_handler(input_text, decision):
            called_with["text"] = input_text
            called_with["category"] = decision.parsed.category
            return "furnished"

        router, _ = self._make_router("furnish", {"furnish": furnish_handler})
        decision, result = asyncio.run(
            router.route(_make_user_messages("the living room needs furniture"))
        )

        assert result == "furnished"
        assert called_with["category"] == "furnish"

    def test_raises_on_unknown_route_without_fallback(self):
        router, _ = self._make_router(
            category="unknown_value",
            routes={"furnish": AsyncMock(return_value="ok")},
        )
        with pytest.raises(StructuredOutputError, match="no route"):
            asyncio.run(router.route(_make_user_messages("something")))

    def test_fallback_used_when_no_match(self):
        async def fallback(input_text, decision):
            return "fallback used"

        router, _ = self._make_router(
            category="mystery",
            routes={"furnish": AsyncMock(return_value="furnished")},
            fallback=fallback,
        )
        decision, result = asyncio.run(router.route(_make_user_messages("hmm")))
        assert result == "fallback used"

    def test_refusal_raises_structured_output_error(self):
        client = MagicMock()
        client.generate_structured = AsyncMock(
            return_value=StructuredOutputResult(parsed=None, refusal="Refused.")
        )
        router = StructuredRouter(
            client=client,
            routing_schema=self._Category,
            routing_key="category",
            routes={"furnish": AsyncMock()},
            system_prompt="classify",
        )
        with pytest.raises(StructuredOutputError, match="refused"):
            asyncio.run(router.route(_make_user_messages("bad input")))

    def test_returns_decision_and_result_tuple(self):
        async def handler(text, decision):
            return {"dispatched": True, "cat": decision.parsed.category}

        router, _ = self._make_router("repair", {"repair": handler})
        decision, result = asyncio.run(
            router.route(_make_user_messages("fix the roof"))
        )

        assert hasattr(decision, "parsed")
        assert decision.parsed.category == "repair"
        assert result["dispatched"] is True
