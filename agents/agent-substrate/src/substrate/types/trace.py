"""TraceContext — the one trace a run belongs to, wherever it executes.

A durable run is not a call stack. It is leased by one worker, suspended for
days, resumed by another, and it spawns children that run elsewhere. A trace
that lives only in process memory ends at the first of those boundaries, and a
fresh random id per attempt (what ``RunMeta.trace_id`` was) never joined
anything: nothing read it.

So the trace context is *data*: persisted with the run, handed to the next
lease, inherited by children. It follows the W3C ``traceparent`` format, which is
what every tracing backend already understands.
"""

from __future__ import annotations

import re
import secrets

from pydantic import field_validator

from substrate.types.content import KernelModel

_TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")
_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_HEX16 = re.compile(r"^[0-9a-f]{16}$")


class TraceContext(KernelModel):
    """A position in a trace: which trace, and which span within it."""

    trace_id: str
    span_id: str
    sampled: bool = True

    @field_validator("trace_id")
    @classmethod
    def _trace_id_is_hex32(cls, value: str) -> str:
        if not _HEX32.match(value) or value == "0" * 32:
            raise ValueError(
                "trace_id must be 32 lowercase hex characters, not all zero"
            )
        return value

    @field_validator("span_id")
    @classmethod
    def _span_id_is_hex16(cls, value: str) -> str:
        if not _HEX16.match(value) or value == "0" * 16:
            raise ValueError(
                "span_id must be 16 lowercase hex characters, not all zero"
            )
        return value

    @classmethod
    def new(cls, *, sampled: bool = True) -> TraceContext:
        """The root of a brand-new trace."""
        return cls(
            trace_id=secrets.token_hex(16),
            span_id=secrets.token_hex(8),
            sampled=sampled,
        )

    def child(self) -> TraceContext:
        """A new span in the same trace, for work this span causes."""
        return TraceContext(
            trace_id=self.trace_id, span_id=secrets.token_hex(8), sampled=self.sampled
        )

    def to_traceparent(self) -> str:
        return f"00-{self.trace_id}-{self.span_id}-{'01' if self.sampled else '00'}"

    @classmethod
    def from_traceparent(cls, header: str) -> TraceContext:
        match = _TRACEPARENT.match(header.strip())
        if match is None:
            raise ValueError(f"not a valid traceparent: {header!r}")
        trace_id, span_id, flags = match.groups()
        return cls(trace_id=trace_id, span_id=span_id, sampled=bool(int(flags, 16) & 1))


__all__ = ["TraceContext"]
