"""Cost-based LLM model router.

Routes queries to the cheapest model that can handle them based on
complexity estimation using the ``ModelProfile`` registry.

Usage::

    from ravi.fabric.llm.router import ModelRouter

    router = ModelRouter()
    model = router.route(messages, tools=tools)
    # → "gpt-4.1-nano" for simple queries, "o3" for complex reasoning
"""

from __future__ import annotations
from ravi.logger import setup_logging

from dataclasses import dataclass
from enum import Enum
from typing import Any

from ravi.fabric.llm.models import ModelProfile, list_models

logger = setup_logging()


class ComplexityTier(str, Enum):
    """Query complexity tiers for routing decisions."""

    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"


@dataclass
class RouteConstraints:
    """Constraints that influence model selection.

    Attributes:
        require_vision: Route to a model with vision support.
        require_tools: Route to a model with tool-calling support.
        require_thinking: Route to a model with extended thinking.
        max_input_cost_per_mtok: Maximum acceptable input cost.
        preferred_providers: Limit to specific providers.
        min_context_length: Minimum context window required.
    """

    require_vision: bool = False
    require_tools: bool = True
    require_thinking: bool = False
    max_input_cost_per_mtok: float | None = None
    preferred_providers: list[str] | None = None
    min_context_length: int = 0


# ── Default model tiers (cheapest per complexity) ─────────────────────────────

_DEFAULT_TIERS: dict[ComplexityTier, list[str]] = {
    ComplexityTier.SIMPLE: [
        "gpt-4.1-nano",
        "gpt-4.1-mini",
        "gemini-2.0-flash",
        "claude-haiku-4-20250514",
    ],
    ComplexityTier.MODERATE: [
        "gpt-4.1",
        "gpt-5.4-mini",
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
    """Route queries to the cheapest suitable model.

    Estimates complexity from message length, tool requirements, and
    explicit hints, then selects the cheapest model from the appropriate
    tier that satisfies the constraints.
    """

    def __init__(
        self,
        tiers: dict[ComplexityTier, list[str]] | None = None,
    ) -> None:
        self._tiers = tiers or _DEFAULT_TIERS

    def estimate_complexity(
        self,
        messages: list[Any],
        *,
        tools: list[Any] | None = None,
        hint: ComplexityTier | None = None,
    ) -> ComplexityTier:
        """Estimate query complexity from context signals.

        Heuristic:
        - Short messages (< 200 chars total), no tools → SIMPLE
        - Tools present or moderate length → MODERATE
        - Thinking required, very long context, or explicit hint → COMPLEX
        """
        if hint is not None:
            return hint

        # Rough character count of all messages
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
        """Select the best model for the given query.

        Returns:
            Model name string (e.g. ``"gpt-4.1-nano"``).
        """
        constraints = constraints or RouteConstraints()
        complexity = self.estimate_complexity(messages, tools=tools, hint=hint)

        # Override to COMPLEX if thinking is explicitly required
        if constraints.require_thinking and complexity != ComplexityTier.COMPLEX:
            complexity = ComplexityTier.COMPLEX

        candidates = self._tiers.get(complexity, [])

        # Filter candidates by constraints
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
            if (
                constraints.preferred_providers
                and profile.provider not in constraints.preferred_providers
            ):
                continue
            if profile.context_length < constraints.min_context_length:
                continue
            valid.append((model_name, profile))

        if not valid:
            # Fallback: return first candidate even if constraints aren't met
            logger.warning(
                "No model satisfies constraints for %s tier, using first candidate",
                complexity.value,
            )
            return candidates[0] if candidates else "gpt-4.1-mini"

        # Sort by input cost (cheapest first)
        valid.sort(key=lambda x: x[1].input_cost_per_mtok)
        selected = valid[0][0]

        logger.debug(
            "Routed to %s (complexity=%s, %d candidates)",
            selected,
            complexity.value,
            len(valid),
        )
        return selected
