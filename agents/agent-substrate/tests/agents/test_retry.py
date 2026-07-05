"""Scheduler retry semantics: re-execution, backoff, and classification.

Covers the fix for a real bug: a journaled error effect used to be
rehydrated by EffectCache.fold() and re-raised from cache on every replay,
so a scheduler retry never actually re-executed the failed operation — it
just replayed the same cached failure until max_retries was exhausted.

1. A retryable (unclassified) failure genuinely re-executes on retry.
2. A PermanentError skips the retry policy and fails on the first attempt.
3. Backoff delay is honored (uses a tiny backoff_s to stay fast).
4. EffectCache.fold() excludes error effects from the rehydrated cache.
"""

from __future__ import annotations

import time

from substrate.agents.runtime import Runtime
from substrate.agents.runtime.backends._event_log import InMemoryEventLog
from substrate.agents.runtime.effect_cache import EffectCache
from substrate.kernel.core.errors import PermanentError
from substrate.kernel.core.identity import AgentId
from substrate.kernel.messaging.message import DataPayload, Message
from substrate.kernel.runtime.log_entry import RunLogEntry
from substrate.kernel.runtime.scheduler import RunRetryPolicy


def _agent_id(name: str) -> AgentId:
    return AgentId(type=name, key="test")


def _msg(target: AgentId) -> Message:
    return Message(target=target, payload=DataPayload(data={}))


async def _run_to_terminal(rt: Runtime, run_id: str) -> str:
    async for entry in rt.event_log.tail(run_id):
        if entry.kind in ("run.completed", "run.failed", "run.cancelled"):
            return entry.kind
    raise AssertionError("run never reached a terminal EventLog entry")


class _FlakyAgent:
    """Fails via a generic (unclassified) exception on the first attempt,
    then succeeds — the classic transient-failure shape a retry should fix."""

    def __init__(self, agent_id: AgentId) -> None:
        self.id = agent_id
        self.attempts = 0

    async def run(self, ctx, inbox) -> None:
        self.attempts += 1
        if self.attempts == 1:
            raise RuntimeError("transient blip")


class _AlwaysCrashingAgent:
    """Raises PermanentError unconditionally — never worth retrying."""

    def __init__(self, agent_id: AgentId) -> None:
        self.id = agent_id
        self.attempts = 0

    async def run(self, ctx, inbox) -> None:
        self.attempts += 1
        raise PermanentError("never going to work")


async def test_retryable_failure_re_executes_on_retry() -> None:
    """A generic (unclassified) failure defaults to retryable and genuinely
    re-executes — not just replays the same cached failure."""
    agent = _FlakyAgent(_agent_id("flaky"))
    async with Runtime() as rt:
        await rt.register(agent)
        run_id = await rt.submit(
            agent.id,
            _msg(agent.id),
            retry_policy=RunRetryPolicy(max_retries=1, backoff_s=0.01),
        )
        outcome = await _run_to_terminal(rt, run_id)

    assert outcome == "run.completed"
    assert agent.attempts == 2, "must genuinely re-execute, not replay the cached error"


async def test_permanent_error_fails_without_retrying() -> None:
    """PermanentError skips the retry policy entirely — one attempt, no backoff."""
    agent = _AlwaysCrashingAgent(_agent_id("permanent"))
    async with Runtime() as rt:
        await rt.register(agent)
        run_id = await rt.submit(
            agent.id,
            _msg(agent.id),
            retry_policy=RunRetryPolicy(max_retries=5, backoff_s=10.0),
        )
        outcome = await _run_to_terminal(rt, run_id)

    assert outcome == "run.failed"
    assert agent.attempts == 1, "a PermanentError must not be retried at all"


async def test_retry_backoff_delays_the_next_attempt() -> None:
    """The retry isn't immediate — it waits at least backoff_s before the
    next attempt (exponential backoff via the suspended+wake_at mechanism)."""
    agent = _FlakyAgent(_agent_id("flaky-timed"))
    backoff_s = 0.2
    async with Runtime() as rt:
        await rt.register(agent)
        start = time.monotonic()
        run_id = await rt.submit(
            agent.id,
            _msg(agent.id),
            retry_policy=RunRetryPolicy(max_retries=1, backoff_s=backoff_s),
        )
        outcome = await _run_to_terminal(rt, run_id)
        elapsed = time.monotonic() - start

    assert outcome == "run.completed"
    assert elapsed >= backoff_s, "retry must not fire before the backoff delay elapses"


async def test_fold_excludes_error_effects_from_cache() -> None:
    """EffectCache.fold() must not rehydrate a failed effect as a cache hit —
    that's what made a scheduler retry replay the same failure forever."""
    event_log = InMemoryEventLog()
    run_id = "run-fold-error-test"

    await event_log.append(
        run_id, RunLogEntry(run_id=run_id, seq=0, kind="run.started"), expected_seq=-1
    )
    await event_log.append(
        run_id,
        RunLogEntry(
            run_id=run_id,
            seq=1,
            kind="effect.result",
            payload={"effect_id": "e1", "status": "error", "value": {"error": "boom"}},
        ),
        expected_seq=0,
    )
    # A successful effect at a different id must still be cached normally.
    await event_log.append(
        run_id,
        RunLogEntry(
            run_id=run_id,
            seq=2,
            kind="effect.result",
            payload={"effect_id": "e2", "status": "ok", "value": {"result": 42}},
        ),
        expected_seq=1,
    )

    cache = await EffectCache.fold(event_log, run_id)

    assert cache.lookup("e1") is None, "error effects must be a cache miss on replay"
    ok_result = cache.lookup("e2")
    assert ok_result is not None and ok_result.status == "ok"


async def test_fold_error_then_success_at_same_effect_id_ends_up_cached() -> None:
    """If a later attempt at the SAME effect_id eventually succeeds, the
    forward scan must leave the success (not the earlier error) in the fold."""
    event_log = InMemoryEventLog()
    run_id = "run-fold-error-then-ok"

    await event_log.append(
        run_id, RunLogEntry(run_id=run_id, seq=0, kind="run.started"), expected_seq=-1
    )
    await event_log.append(
        run_id,
        RunLogEntry(
            run_id=run_id,
            seq=1,
            kind="effect.result",
            payload={"effect_id": "e1", "status": "error", "value": {}},
        ),
        expected_seq=0,
    )
    await event_log.append(
        run_id,
        RunLogEntry(
            run_id=run_id,
            seq=2,
            kind="effect.result",
            payload={"effect_id": "e1", "status": "ok", "value": {"result": "done"}},
        ),
        expected_seq=1,
    )

    cache = await EffectCache.fold(event_log, run_id)
    result = cache.lookup("e1")
    assert result is not None
    assert result.status == "ok"
    assert result.value == {"result": "done"}


async def test_retry_and_suspension_are_reflected_in_otel_counters() -> None:
    """The Scheduler backends emit substrate.runtime.retries/.suspensions
    counters (see infrastructure/observability/runtime_metrics.py) — this is
    the only place they're exercised end-to-end. Uses a temporary
    MeterProvider with InMemoryMetricReader so it doesn't depend on (or
    pollute) any real OTLP configuration."""
    from opentelemetry import metrics as otel_metrics
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    from substrate.agents.runtime.backends._scheduler import InMemoryScheduler

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    original_provider = otel_metrics.get_meter_provider()
    otel_metrics.set_meter_provider(provider)
    try:
        agent = _FlakyAgent(_agent_id("flaky-metrics"))
        async with Runtime() as rt:
            await rt.register(agent)
            run_id = await rt.submit(
                agent.id,
                _msg(agent.id),
                retry_policy=RunRetryPolicy(max_retries=1, backoff_s=0.01),
            )
            await _run_to_terminal(rt, run_id)

        # A plain SUSPENDED release (not via retry) — exercise the other counter.
        sched = InMemoryScheduler()
        from substrate.kernel.runtime.ids import RunStatus, new_run_id

        suspend_run_id = new_run_id()
        sched.register_run(suspend_run_id, _agent_id("suspend-metrics"))
        await sched.enqueue(suspend_run_id, priority=5, tenant="default")
        leases = await sched.lease(worker_id="w1", capacity=10)
        lease = next(lease for lease in leases if lease.run_id == suspend_run_id)
        await sched.release(lease, status=RunStatus.SUSPENDED)

        data = reader.get_metrics_data()
        counter_names: set[str] = set()
        for rm in data.resource_metrics:
            for sm in rm.scope_metrics:
                for metric in sm.metrics:
                    counter_names.add(metric.name)

        assert "substrate.runtime.retries" in counter_names
        assert "substrate.runtime.suspensions" in counter_names
    finally:
        otel_metrics.set_meter_provider(original_provider)
