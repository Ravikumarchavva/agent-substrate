"""Token usage contract — shared by LLM clients and stream events."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Usage:
    """Token usage for a single LLM call.

    ``cached_tokens`` counts tokens served from the provider's prompt cache
    (Anthropic cache_read_input_tokens, OpenAI cached_tokens). These are
    already included in ``input_tokens`` — broken out so callers can compute
    accurate cost (cached tokens are billed at a lower rate).
    """

    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            cached_tokens=self.cached_tokens + other.cached_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


__all__ = ["Usage"]
