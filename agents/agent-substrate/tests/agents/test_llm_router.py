from __future__ import annotations

from ravi.agents.llm.router import ModelRouter, ComplexityTier, RouteConstraints


class DummyMessage:
    def __init__(self, content):
        self.content = content


def test_estimate_complexity_hint():
    router = ModelRouter()
    assert (
        router.estimate_complexity([], hint=ComplexityTier.COMPLEX)
        == ComplexityTier.COMPLEX
    )


def test_estimate_complexity_messages():
    router = ModelRouter()

    # Simple (short message, no tools)
    msg_simple = [DummyMessage(content="hi")]
    assert router.estimate_complexity(msg_simple) == ComplexityTier.SIMPLE

    # Moderate (longer message)
    msg_mod = [DummyMessage(content="a" * 600)]
    assert router.estimate_complexity(msg_mod) == ComplexityTier.MODERATE

    # Complex (very long message)
    msg_complex = [DummyMessage(content="a" * 10001)]
    assert router.estimate_complexity(msg_complex) == ComplexityTier.COMPLEX


def test_route_model_sorting():
    router = ModelRouter(
        tiers={ComplexityTier.SIMPLE: ["gpt-4.1-mini", "claude-haiku-4-20250514"]}
    )

    msg = [DummyMessage(content="ping")]
    selected = router.route(msg)
    assert selected in {"gpt-4.1-mini", "claude-haiku-4-20250514"}


def test_route_constraints():
    router = ModelRouter(tiers={ComplexityTier.SIMPLE: ["gpt-4.1-mini"]})
    msg = [DummyMessage(content="ping")]
    constraints = RouteConstraints(require_vision=True)
    selected = router.route(msg, constraints=constraints)
    assert selected == "gpt-4.1-mini"
