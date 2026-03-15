"""Typed container for structured LLM outputs.

``StructuredOutputResult[T]`` is the value returned by every structured
output entry point in the framework (``generate_structured``, ``parse``,
``run_structured``).  It carries:

  • ``parsed``   — the validated Pydantic instance, or ``None`` on refusal
  • ``raw_text`` — the raw JSON string the model produced (useful for
                   debugging or audit)
  • ``refusal``  — the refusal message when the model safety-refuses;
                   ``None`` on successful parse

``StructuredOutputError`` is raised when the model returns something that
cannot be parsed at all (malformed JSON, schema validation failure after
retries, etc.).  A safety refusal is *not* an error — it surfaces as
``parsed=None, refusal="<message>"``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, Optional, TypeVar

T = TypeVar("T")


@dataclass
class StructuredOutputResult(Generic[T]):
    """The result of a structured output call.

    Attributes:
        parsed: The validated Pydantic instance.  ``None`` when the model
            issued a safety refusal.
        raw_text: The raw JSON string as returned by the model.  Empty
            string when a refusal occurred before any text was generated.
        refusal: The refusal message emitted by the model when it declines
            to answer.  ``None`` on a successful parse.
        model: Optional model identifier for tracing / logging.
    """

    parsed: Optional[T]
    raw_text: str = field(default="")
    refusal: Optional[str] = field(default=None)
    model: Optional[str] = field(default=None)

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    @property
    def refused(self) -> bool:
        """``True`` when the model safety-refused the request."""
        return self.refusal is not None

    @property
    def ok(self) -> bool:
        """``True`` when a parsed value is available (no refusal, no error)."""
        return self.parsed is not None and self.refusal is None

    def unwrap(self) -> T:
        """Return ``parsed`` or raise ``StructuredOutputError``.

        Use when you expect a valid result and want an early crash rather
        than a downstream ``None``-dereference.

        Raises:
            StructuredOutputError: If ``parsed`` is ``None`` (refusal or
                unrecoverable parse failure).
        """
        if self.parsed is None:
            raise StructuredOutputError(
                f"No parsed value available. "
                f"{'Refusal: ' + self.refusal if self.refusal else 'Unknown parse failure.'}"
            )
        return self.parsed


class StructuredOutputError(Exception):
    """Raised when a structured output call cannot produce a valid parse.

    This is distinct from a model *refusal* (which surfaces as
    ``StructuredOutputResult(parsed=None, refusal="...")``) and indicates
    an unrecoverable error such as:
      - Malformed JSON returned by the model
      - Schema validation failure after the configured retry budget
      - API error during the structured output call
    """
