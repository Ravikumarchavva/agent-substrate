"""Metric instruments.

Created lazily from the OpenTelemetry *API* meter, which is a no-op until an
application configures an SDK. Labels are bounded: model, provider, tool,
outcome, agent. Never a run, user or thread id — those would make a time series
per request and take down the metrics backend.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from opentelemetry import metrics

from substrate.telemetry import semconv

_METER_NAME = "substrate"


class Instruments:
    def __init__(self) -> None:
        meter = metrics.get_meter(_METER_NAME)
        self.run_duration = meter.create_histogram(
            semconv.M_RUN_DURATION, unit="s", description="Wall-clock time from start to a terminal state."
        )
        self.runs = meter.create_counter(semconv.M_RUNS, description="Runs reaching a terminal state, by outcome.")
        self.retries = meter.create_counter(semconv.M_RETRIES, description="Runs re-enqueued after a retryable failure.")
        self.suspensions = meter.create_counter(semconv.M_SUSPENSIONS, description="Runs parked dormant.")
        self.replay_hits = meter.create_counter(
            semconv.M_REPLAY_HITS, description="Journaled steps served from the journal instead of executed."
        )
        self.dead_letters = meter.create_counter(semconv.M_DEAD_LETTERS, description="Messages moved to the dead-letter queue.")
        self.tool_calls = meter.create_counter(semconv.M_TOOL_CALLS, description="Tool invocations, by tool and outcome.")
        self.tool_duration = meter.create_histogram(semconv.M_TOOL_DURATION, unit="s", description="Tool execution time.")
        self.llm_duration = meter.create_histogram(
            semconv.M_LLM_DURATION, unit="s", description="LLM call duration (GenAI convention)."
        )
        self.llm_tokens = meter.create_histogram(
            semconv.M_LLM_TOKENS, unit="{token}", description="Tokens used per LLM call (GenAI convention)."
        )
        self.llm_cost = meter.create_counter(semconv.M_LLM_COST, unit="USD", description="Estimated LLM spend.")
        self.llm_errors = meter.create_counter(semconv.M_LLM_ERRORS, description="Failed LLM calls, by error code.")
        self._meter = meter

    def observe_queue(self, callback: Any) -> None:
        """Register gauges for queue depth and oldest-lease age. ``callback`` is an
        OpenTelemetry observable-gauge callback supplied by the running engine."""
        self._meter.create_observable_gauge(
            semconv.M_QUEUE_DEPTH, callbacks=[callback], description="Runs waiting for a worker."
        )


@lru_cache(maxsize=1)
def instruments() -> Instruments:
    return Instruments()


__all__ = ["Instruments", "instruments"]
