"""Cost-based LLM model router.

Routes queries to the cheapest model that can handle them based on
complexity estimation using the ``ModelProfile`` registry.

Usage::

    from ravi.agents.llm.router import ModelRouter

    router = ModelRouter()
    model = router.route(messages, tools=tools)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from ravi.agents.llm.models import ModelProfile, list_models
from ravi.logger import setup_logging

logger = setup_logging()


class ComplexityTier(str, Enum):
    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"


@dataclass
class RouteConstraints:
    require_vision: bool = False
    require_tools: bool = True
    require_thinking: bool = False
    max_input_cost_per_mtok: float | None = None
    preferred_providers: list[str] | None = None
    min_context_length: int = 0


_DEFAULT_TIERS: dict[ComplexityTier, list[str]] = {
    ComplexityTier.SIMPLE: [
        "gpt-4.1-nano",
        "gpt-4.1-mini",
        "gemini-2.0-flash",
        "claude-haiku-4-20250514",
    ],
    ComplexityTier.MODERATE: [
        "gpt-4.1",
        "gpt-5-mini",
        "gpt-4o",
        "gemini-2.5-flash",
        "claude-sonnet-4-20250514",
    ],
    ComplexityTier.COMPLEX: [
        "o3",
        "o4-mini",
        "gemini-2.5-pro",
        "claude-opus-4-20250514",
    ],
}


class ModelRouter:
    """Route queries to the cheapest suitable model."""

    def __init__(self, tiers: dict[ComplexityTier, list[str]] | None = None) -> None:
        self._tiers = tiers or _DEFAULT_TIERS

    def estimate_complexity(
        self,
        messages: list[Any],
        *,
        tools: list[Any] | None = None,
        hint: ComplexityTier | None = None,
    ) -> ComplexityTier:
        if hint is not None:
            return hint

        total_chars = 0
        for msg in messages:
            content = getattr(msg, "content", None)
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, str):
                        total_chars += len(part)

        has_tools = bool(tools)
        if total_chars > 10_000 or (has_tools and total_chars > 2_000):
            return ComplexityTier.COMPLEX
        if has_tools or total_chars > 500:
            return ComplexityTier.MODERATE
        return ComplexityTier.SIMPLE

    def route(
        self,
        messages: list[Any],
        *,
        tools: list[Any] | None = None,
        constraints: RouteConstraints | None = None,
        hint: ComplexityTier | None = None,
    ) -> str:
        constraints = constraints or RouteConstraints()
        complexity = self.estimate_complexity(messages, tools=tools, hint=hint)

        if constraints.require_thinking and complexity != ComplexityTier.COMPLEX:
            complexity = ComplexityTier.COMPLEX

        candidates = self._tiers.get(complexity, [])
        all_profiles = {m.name: m for m in list_models()}
        valid: list[tuple[str, ModelProfile]] = []

        for model_name in candidates:
            profile = all_profiles.get(model_name)
            if profile is None:
                continue
            if constraints.require_vision and not profile.supports_vision:
                continue
            if constraints.require_tools and not profile.supports_tools:
                continue
            if constraints.require_thinking and not profile.supports_thinking:
                continue
            if (
                constraints.max_input_cost_per_mtok is not None
                and profile.input_cost_per_mtok > constraints.max_input_cost_per_mtok
            ):
                continue
            if constraints.preferred_providers and profile.provider not in constraints.preferred_providers:
                continue
            if profile.context_length < constraints.min_context_length:
                continue
            valid.append((model_name, profile))

        if not valid:
            logger.warning("No model satisfies constraints for %s tier, using first candidate", complexity.value)
            return candidates[0] if candidates else "gpt-4.1-mini"

        valid.sort(key=lambda x: x[1].input_cost_per_mtok)
        selected = valid[0][0]
        logger.debug("Routed to %s (complexity=%s, %d candidates)", selected, complexity.value, len(valid))
        return selected
