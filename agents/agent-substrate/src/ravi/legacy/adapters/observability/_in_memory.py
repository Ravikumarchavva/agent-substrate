"""In-memory observability and replay reference implementations.

These classes satisfy the observability Protocols in
``ravi.adapters.observability`` without external infrastructure. They are intentionally small and deterministic
so tests and local runtimes can exercise observability, replay gating, and
operator kill switches without wiring an exporter or control plane service.

Thread-safety
~~~~~~~~~~~~~
Each implementation guards all shared mutable state with ``threading.RLock``
so concurrent agent loops, replay workers, and operator control threads can
interleave safely under free-threaded Python.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import replace
from datetime import datetime

from ravi.adapters.observability._spans import EnvelopeSpan, SpanQuery, SpanStatus
from ravi.adapters.observability._replay import (
    ReplayAdmission,
    ReplayAdmissionStatus,
    ReplayDenyRule,
    ReplayRequest,
)

__all__ = [
    "InMemoryEnvelopeSpanRecorder",
    "InMemoryReplayGate",
]


class InMemoryEnvelopeSpanRecorder:
    """RLock-guarded in-process span recorder."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._spans: dict[str, EnvelopeSpan] = {}
        self._by_envelope: dict[str, list[str]] = {}
        self._by_correlation: dict[str, list[str]] = {}

    async def start_span(self, span: EnvelopeSpan) -> EnvelopeSpan:
        with self._lock:
            existing = self._spans.get(span.span_id)
            if existing is not None:
                return existing

            self._spans[span.span_id] = span
            self._by_envelope.setdefault(span.envelope_id, []).append(span.span_id)
            self._by_correlation.setdefault(span.correlation_id, []).append(
                span.span_id
            )
            return span

    async def finish_span(
        self,
        span_id: str,
        *,
        status: SpanStatus = SpanStatus.OK,
        ended_at: datetime | None = None,
        attributes: tuple[tuple[str, str], ...] = (),
    ) -> EnvelopeSpan:
        with self._lock:
            span = self._spans.get(span_id)
            if span is None:
                raise KeyError(span_id)
            if span.is_finished:
                return span
            finished = span.finish(
                status=status,
                ended_at=ended_at,
                attributes=attributes,
            )
            self._spans[span_id] = finished
            return finished

    async def span_for(self, span_id: str) -> EnvelopeSpan | None:
        with self._lock:
            return self._spans.get(span_id)

    async def spans_for_envelope(self, envelope_id: str) -> tuple[EnvelopeSpan, ...]:
        with self._lock:
            return self._snapshot(self._by_envelope.get(envelope_id, ()))

    async def spans_for_correlation(
        self, correlation_id: str
    ) -> tuple[EnvelopeSpan, ...]:
        with self._lock:
            return self._snapshot(self._by_correlation.get(correlation_id, ()))

    async def query_spans(self, query: SpanQuery) -> tuple[EnvelopeSpan, ...]:
        with self._lock:
            if query.envelope_id is not None:
                candidates = list(self._by_envelope.get(query.envelope_id, ()))
            elif query.correlation_id is not None:
                candidates = list(self._by_correlation.get(query.correlation_id, ()))
            else:
                candidates = list(self._spans)

            matches: list[EnvelopeSpan] = []
            for span_id in candidates:
                span = self._spans[span_id]
                if _span_matches(span, query):
                    matches.append(span)
                    if len(matches) >= query.limit:
                        break
            return tuple(matches)

    def count(self) -> int:
        """Return the current number of recorded spans."""

        with self._lock:
            return len(self._spans)

    def _snapshot(self, span_ids: object) -> tuple[EnvelopeSpan, ...]:
        return tuple(self._spans[span_id] for span_id in span_ids)


def _span_matches(span: EnvelopeSpan, query: SpanQuery) -> bool:
    if query.envelope_id is not None and span.envelope_id != query.envelope_id:
        return False
    if query.correlation_id is not None and span.correlation_id != query.correlation_id:
        return False
    if query.trace_id is not None and span.trace_id != query.trace_id:
        return False
    if query.tenant_id is not None and span.tenant_id != query.tenant_id:
        return False
    if query.workspace_id is not None and span.workspace_id != query.workspace_id:
        return False
    return query.status is None or span.status is query.status


class InMemoryReplayGate:
    """RLock-guarded replay gate with idempotent admissions."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._admissions: dict[str, ReplayAdmission] = {}
        self._deny_rules: dict[str, ReplayDenyRule] = {}

    async def admit(self, request: ReplayRequest) -> ReplayAdmission:
        with self._lock:
            prior = self._admissions.get(request.idempotency_key)
            if prior is not None:
                return replace(prior, status=ReplayAdmissionStatus.DUPLICATE)

            deny_rule = self._matching_denial(request)
            if deny_rule is None:
                decision = ReplayAdmission(
                    idempotency_key=request.idempotency_key,
                    envelope_id=request.envelope_id,
                    correlation_id=request.correlation_id,
                    allowed=True,
                    status=ReplayAdmissionStatus.ALLOWED,
                    reason="allowed",
                    decided_at=datetime.now().astimezone(),
                    replay_token=uuid.uuid4().hex,
                )
            else:
                decision = ReplayAdmission(
                    idempotency_key=request.idempotency_key,
                    envelope_id=request.envelope_id,
                    correlation_id=request.correlation_id,
                    allowed=False,
                    status=ReplayAdmissionStatus.DENIED,
                    reason=deny_rule.reason,
                    decided_at=datetime.now().astimezone(),
                    replay_token=None,
                )
            self._admissions[request.idempotency_key] = decision
            return decision

    async def admission_for(
        self, idempotency_key: str
    ) -> ReplayAdmission | None:
        with self._lock:
            return self._admissions.get(idempotency_key)

    async def deny(self, rule: ReplayDenyRule) -> None:
        with self._lock:
            self._deny_rules[rule.rule_id] = rule

    async def clear_denial(self, rule_id: str) -> bool:
        with self._lock:
            return self._deny_rules.pop(rule_id, None) is not None

    async def deny_rules(self) -> tuple[ReplayDenyRule, ...]:
        """Return a snapshot of active replay deny rules."""

        with self._lock:
            return tuple(self._deny_rules.values())

    def admission_count(self) -> int:
        """Return the number of unique idempotency keys admitted."""

        with self._lock:
            return len(self._admissions)

    def _matching_denial(self, request: ReplayRequest) -> ReplayDenyRule | None:
        for rule in self._deny_rules.values():
            if rule.matches(request):
                return rule
        return None
