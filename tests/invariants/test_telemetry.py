"""Invariant register — observability (rows I24, I25).

A turn should be one trace: a run span, with the LLM call and each tool call
nested inside it. What the tracing middleware produces instead is four
unparented root spans, because it starts spans without making them current,
and then sets its most useful attributes — status, token count, tool outcome —
after the span has already ended, where they are silently discarded.

Row I25 passes today and is here to stay passing: telemetry is sampled, shipped
to third parties and retained outside the erasure path, so prompt and response
text must never ride along in it by default.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

pytest.importorskip("opentelemetry.sdk")

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from substrate.kernel.context import ContextConfig
from substrate.kernel.agents.react import ReActAgent
from substrate.kernel.middleware.observability import (
    AgentTracingMiddleware,
    ChatTracingMiddleware,
    FunctionTracingMiddleware,
)
from substrate.kernel.middleware.pipeline import MiddlewarePipeline
from substrate.kernel.runtime.runtime import Runtime
from substrate.kernel.storage.history import InMemoryHistoryProvider
from substrate.kernel.tools.toolbox import Toolbox
from substrate.kernel.abstractions.core.content import ChatMessage, Role, TextBlock, ToolUseBlock
from substrate.kernel.abstractions.core.identity import Actor
from substrate.kernel.abstractions.messaging.message import ChatPayload, Message

from tests.invariants._harness.doubles import ScriptedLLM
from tests.invariants._harness.scenarios import ChargeCard

SECRET = "the-user-said-something-private"


def _spans() -> list[Any]:
    """One real ReAct turn — a user message, an LLM call that asks for a tool, the
    tool, then an LLM call that answers — under the three tracing middlewares.

    A full turn is the point: it is what produces a turn span, two LLM spans and
    a tool span, so their relationship can be asserted at all. A bare agent
    makes a single span, and "one trace" then holds for the wrong reason.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    tools = Toolbox()
    tools.add(ChargeCard([]))
    llm = ScriptedLLM(
        [
            [ToolUseBlock(call_id="c1", tool_name="charge_card", arguments={"amount": 100})],
            [TextBlock(text="all done")],
        ]
    )
    agent = ReActAgent(
        "traced",
        model=llm,
        tools=tools,
        context=ContextConfig(history=InMemoryHistoryProvider()),
        middleware=MiddlewarePipeline(
            [AgentTracingMiddleware(), ChatTracingMiddleware(), FunctionTracingMiddleware()]
        ),
    )

    async def _run() -> None:
        async with Runtime() as runtime:
            await runtime.register(agent)
            run_id = await runtime.submit(
                agent.id,
                Message(
                    target=agent.id,
                    sender=Actor(type="proxy", key="user"),
                    payload=ChatPayload(
                        message=ChatMessage(role=Role.USER, content=[TextBlock(text=SECRET)])
                    ),
                ),
            )
            async for entry in runtime.event_log.tail(run_id):
                if entry.kind in ("run.completed", "run.failed"):
                    return

    asyncio.run(asyncio.wait_for(_run(), timeout=20))
    return list(exporter.get_finished_spans())


@pytest.fixture(scope="module")
def spans() -> list[Any]:
    return _spans()


@pytest.mark.xfail(
    strict=True,
    reason="I24: spans are started without being made current, so each one is "
    "its own root and a turn produces several disconnected traces. Fixed in "
    "step 2 (one telemetry module).",
)
def test_i24_one_turn_is_one_trace(spans: list[Any]) -> None:
    """A trace that is four traces cannot answer 'what did this run do', which
    is the only question it exists to answer."""
    assert len(spans) > 1, (
        "a full turn produced no nesting to assert about; the scenario is not "
        f"exercising the middlewares: {[s.name for s in spans]}"
    )
    trace_ids = {span.context.trace_id for span in spans}
    assert len(trace_ids) == 1, (
        f"{len(spans)} spans landed in {len(trace_ids)} separate traces: "
        + ", ".join(f"{s.name}@{s.context.trace_id:032x}" for s in spans)
    )


@pytest.mark.xfail(
    strict=True,
    reason="I24: only the agent-turn span has no parent; the llm and tool "
    "spans are roots too. Fixed in step 2.",
)
def test_i24_only_the_run_span_is_a_root(spans: list[Any]) -> None:
    assert len(spans) > 1, (
        f"the scenario produced no nesting to assert about: {[s.name for s in spans]}"
    )
    roots = [span.name for span in spans if span.parent is None]
    assert len(roots) == 1, f"expected a single root span, got roots: {roots}"


@pytest.mark.xfail(
    strict=True,
    reason="I24: outcome attributes are set after the span is ended, where "
    "OpenTelemetry discards them ('Setting attribute on ended span'). Fixed in "
    "step 2.",
)
def test_i24_spans_carry_their_outcome(spans: list[Any]) -> None:
    """A span with no outcome recorded is a timing bar and nothing more."""
    tool_spans = [s for s in spans if "tool_call" in s.name]
    assert tool_spans, f"no tool span among {[s.name for s in spans]}"
    for span in tool_spans:
        assert "tool.ok" in (span.attributes or {}), (
            f"{span.name} recorded no outcome; attributes: {dict(span.attributes or {})}"
        )


def test_i25_no_prompt_or_tool_content_appears_in_span_attributes(
    spans: list[Any],
) -> None:
    """Telemetry leaves the erasure boundary: it is sampled, exported to third
    parties and retained on their schedule. Content goes in it only when a
    deployment explicitly opts in."""
    leaked = [
        (span.name, key)
        for span in spans
        for key, value in (span.attributes or {}).items()
        if isinstance(value, str) and SECRET in value
    ]
    assert not leaked, f"user content appeared in span attributes: {leaked}"
