"""Token usage contract — shared by LLM clients and stream events.

Deliberately a frozen ``dataclass``, not a pydantic model, despite the
kernel's general rule (pydantic for persisted/wire types, dataclass for
pure in-process values): ``Usage`` is never independently persisted or
sent over the wire on its own — it's always nested inside another
pydantic type (``LLMResponse.usage``, log payload dicts) that handles its
own (de)serialization, and it's constructed and ``__add__``-accumulated on
every single LLM call, a genuinely hot path where a slotted dataclass's
lower construction overhead is the right trade-off.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Usage:
    """Token usage for a single LLM call.

    ``cached_tokens`` counts tokens served from the provider's prompt cache
    (Anthropic cache_read_input_tokens, OpenAI cached_tokens). These are
    already included in ``input_tokens`` — broken out so callers can compute
    accurate cost (cached tokens are billed at a lower rate).

    ``reasoning_tokens`` counts tokens used for extended thinking / chain-of-
    thought (Anthropic extended thinking, OpenAI o-series). These are included
    in ``output_tokens`` — broken out for cost attribution.
    """

    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            cached_tokens=self.cached_tokens + other.cached_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
        )


__all__ = ["Usage"]
