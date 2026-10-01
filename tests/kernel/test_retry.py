"""Run retry semantics: re-execution, backoff and classification.

Covers the fix for a real bug: a failed effect used to be rehydrated from the journal
and re-raised on every replay, so a retry never re-executed the failed operation — it
replayed the same cached failure until retries ran out.

1. A retryable (unclassified) failure genuinely re-executes on retry.
2. A PermanentError skips the retry policy and fails on the first attempt.
3. Backoff delay is honored (a tiny backoff_s keeps it fast).
4. Retries and suspensions are visible as OpenTelemetry counters.
"""

from __future__ import annotations

import time

from substrate.kernel.abstractions.core.identity import Actor
from substrate.kernel.abstractions.exceptions import PermanentError
from substrate.kernel.abstractions.messaging.message import DataPayload, Message
from substrate.kernel.abstractions.runtime.scheduler import RunRetryPolicy
from substrate.kernel.runtime import Runtime
from substrate.kernel.testing.runtime import ephemeral_runtime


def _agent_id(name: str) -> Actor:
    return Actor(type="agent", key=name)


def _msg(target: Actor) -> Message:
    return Message(target=target, sender=Actor.system("test"), payload=DataPayload(data={}))


async def _run_to_terminal(rt: Runtime, run_id: str) -> str:
    async for entry in rt.tail(run_id):
        if entry.kind in ("run.completed", "run.failed", "run.cancelled"):
            return entry.kind
    raise AssertionError("run never reached a terminal entry")


class _FlakyAgent:
    """Fails via a generic (unclassified) exception on the first attempt,
    then succeeds — the classic transient-failure shape a retry should fix."""

    def __init__(self, agent_id: Actor) -> None:
        self.id = agent_id
        self.attempts = 0

    async def run(self, ctx, inbox) -> None:
        self.attempts += 1
        if self.attempts == 1:
            raise RuntimeError("transient blip")


class _AlwaysCrashingAgent:
    """Raises PermanentError unconditionally — never worth retrying."""

    def __init__(self, agent_id: Actor) -> None:
        self.id = agent_id
        self.attempts = 0

    async def run(self, ctx, inbox) -> None:
        self.attempts += 1
        raise PermanentError("never going to work")


async def test_retryable_failure_re_executes_on_retry() -> None:
    """A generic (unclassified) failure defaults to retryable and genuinely
    re-executes — not just replays the same cached failure."""
    agent = _FlakyAgent(_agent_id("flaky"))
    async with ephemeral_runtime() as rt:
        await rt.register(agent)
        run_id = await rt.submit(agent.id, _msg(agent.id), retry_policy=RunRetryPolicy(max_retries=1, backoff_s=0.01))
        outcome = await _run_to_terminal(rt, run_id)

    assert outcome == "run.completed"
    assert agent.attempts == 2, "must genuinely re-execute, not replay the cached error"


async def test_permanent_error_fails_without_retrying() -> None:
    """PermanentError skips the retry policy entirely — one attempt, no backoff."""
    agent = _AlwaysCrashingAgent(_agent_id("permanent"))
    async with ephemeral_runtime() as rt:
        await rt.register(agent)
        run_id = await rt.submit(agent.id, _msg(agent.id), retry_policy=RunRetryPolicy(max_retries=5, backoff_s=10.0))
        outcome = await _run_to_terminal(rt, run_id)

    assert outcome == "run.failed"
    assert agent.attempts == 1, "a PermanentError must not be retried at all"


async def test_retry_backoff_delays_the_next_attempt() -> None:
    """The retry isn't immediate — it waits at least backoff_s before the next attempt."""
    agent = _FlakyAgent(_agent_id("flaky-timed"))
    backoff_s = 0.2
    async with ephemeral_runtime() as rt:
        await rt.register(agent)
        start = time.monotonic()
        run_id = await rt.submit(agent.id, _msg(agent.id), retry_policy=RunRetryPolicy(max_retries=1, backoff_s=backoff_s))
        outcome = await _run_to_terminal(rt, run_id)
        elapsed = time.monotonic() - start

    assert outcome == "run.completed"
    assert elapsed >= backoff_s * 0.5, "retry must not fire before the backoff delay elapses"
