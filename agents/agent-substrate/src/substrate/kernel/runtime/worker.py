"""Worker — leases runs from the store and drives each through its agent.

Each leased run is an asyncio task, but the task does not outlive a suspension. When
an agent waits on something not yet available, ``RunContext`` raises
``SuspendInterrupt``, which unwinds out of ``agent.run`` to here; the worker commits
the suspension and the task ends. The run then costs nothing until a message, signal,
timer or finished child wakes it, at which point any worker leases it, folds its
journal and calls ``agent.run`` again from the top. Every step already done answers
from the journal, so execution fast-forwards back to the wait that now succeeds.

Every way a run can end is **one commit**: the journal entry, the status, the inbox
acknowledgement and the signal that wakes a parent land together or not at all. The
commit carries the lease's epoch, so a worker that was paused past its lease and has
since been replaced cannot write anything.

What stops a run
----------------
* the agent returns            -> ``Complete``
* the agent raises             -> ``Retry`` (backoff) while retries remain, else ``Fail``
* a policy halt (guardrail, budget, permanent error) -> ``Fail``, never retried: the
  same inputs would produce the same decision.
* cancellation                 -> ``Cancel``
* the lease is lost            -> nothing: the run now belongs to someone else
* the process is shutting down -> nothing: the lease expires and the run resumes
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from substrate.kernel.abstractions.agent.runtime_context import RunMeta
from substrate.kernel.abstractions.core.error_info import ErrorInfo
from substrate.kernel.abstractions.exceptions import (
    AgentCrashError,
    BudgetExhaustedError,
    CancellationError,
    KernelError,
    LeaseLostError,
    MiddlewareTermination,
    NonDeterminismError,
    PermanentError,
    SuspendInterrupt,
)
from substrate.kernel.abstractions.runtime.scheduler import RunRetryPolicy
from substrate.kernel.abstractions.runtime.log_entry import RunLogKind
from substrate.kernel.abstractions.runtime.store import (
    Cancel,
    Commit,
    Complete,
    Fail,
    HeartbeatResult,
    Lease,
    Nack,
    NewEntry,
    Retry,
    RuntimeStore,
    Suspend,
)
from substrate.kernel.runtime.cancellation import CancellationToken
from substrate.kernel.runtime.context import RunContext
from substrate.kernel.runtime.journal import Journal
from substrate.kernel.telemetry import instruments, semconv, span
from substrate.logger import setup_logging

if TYPE_CHECKING:
    from substrate.kernel.runtime.resolver import ActorResolver

logger = setup_logging()

# A heartbeat that fails this many times in a row is treated as a lost lease: the
# store is unreachable, so this worker can no longer prove it still owns the run.
_HEARTBEAT_FAILURES_BEFORE_LOST = 3


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def classify_failure(exc: BaseException, *, run_id: str, agent_id: Any) -> tuple[ErrorInfo, bool]:
    """How a failed attempt should be recorded, and whether trying again can help.

    A guardrail trip, an exhausted budget or a permanent error is a deterministic
    decision: replaying it produces the same one, and retrying only burns a lease.
    Anything else is treated as transient — the default for an unexpected crash.
    """
    if isinstance(exc, MiddlewareTermination):
        return ErrorInfo(code="guardrail_tripped", message=f"Request blocked: {exc.message or exc}"), False
    if isinstance(exc, BudgetExhaustedError):
        return ErrorInfo(code="budget_exhausted", message=str(exc)), False
    if isinstance(exc, (PermanentError, NonDeterminismError)):
        return ErrorInfo(code="permanent_error", message=str(exc)), False
    if isinstance(exc, KernelError):
        return exc.to_info().model_copy(update={"code": "agent_crashed"}), exc.retryable
    crash = AgentCrashError(str(exc), run_id=run_id, agent_id=agent_id)
    return ErrorInfo(code="agent_crashed", message=str(crash), retryable=True), True


def backoff(policy: RunRetryPolicy, retry_count: int) -> float:
    """Exponential backoff with jitter, capped at the policy's ceiling. Jitter keeps a
    batch of runs that failed together from retrying together."""
    delay = min(policy.backoff_s * (2 ** retry_count), policy.max_backoff_s)
    return delay * random.uniform(0.8, 1.2) if delay else 0.0


class Worker:
    """Leases runs and executes them. Any number may share one store."""

    def __init__(
        self,
        *,
        worker_id: str,
        store: RuntimeStore,
        resolver: ActorResolver,
        max_concurrency: int = 10,
        lease_s: float = 30.0,
        poll_interval_s: float = 0.05,
    ) -> None:
        self._worker_id = worker_id
        self._store = store
        self._resolver = resolver
        self._max_concurrency = max_concurrency
        self._lease_s = lease_s
        self._poll_interval_s = poll_interval_s
        self._running = False
        self._poll_task: asyncio.Task[None] | None = None
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._tokens: dict[str, CancellationToken] = {}
        # Why a task was asked to stop, so the handler can tell a cancel from a shutdown.
        self._stop_reason: dict[str, str] = {}
        self._stats_task: asyncio.Task[None] | None = None
        self._last_stats: dict[str, float] = {}

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        self._running = True
        self._poll_task = asyncio.create_task(self._poll(), name="worker-poll")
        self._stats_task = asyncio.create_task(self._publish_stats(), name="worker-stats")

    async def stop(self) -> None:
        self._running = False
        for task in (self._poll_task, self._stats_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        running = list(self._tasks.items())
        for run_id, task in running:
            self._stop_reason.setdefault(run_id, "shutdown")
            task.cancel()
        if running:
            await asyncio.gather(*(t for _, t in running), return_exceptions=True)

    def cancel_local(self, run_id: str, reason: str = "cancelled") -> bool:
        """Interrupt a run this worker is executing. The store's cancel flag reaches
        runs on other workers through their heartbeat; this is the fast path for ours."""
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        self._stop_reason[run_id] = f"cancel:{reason}"
        token = self._tokens.get(run_id)
        if token is not None:
            token.cancel(reason)
        task.cancel()
        return True

    # ------------------------------------------------------------------ polling

    async def _poll(self) -> None:
        while self._running:
            try:
                free = self._max_concurrency - len(self._tasks)
                leases = (
                    await self._store.lease(worker_id=self._worker_id, capacity=free, lease_s=self._lease_s, now=_now())
                    if free > 0
                    else []
                )
                for lease in leases:
                    agent = await self._resolver.resolve(lease.agent)
                    if agent is None:
                        # Nothing here can run it yet (a cold start before registration
                        # finishes). Holding the lease lets it expire and the run be
                        # reclaimed once the agent is registered.
                        logger.warning("agent %s is not registered; leaving run %s to be reclaimed", lease.agent, lease.run_id)
                        continue
                    self._tasks[lease.run_id] = asyncio.create_task(self._run(lease, agent), name=f"run-{lease.run_id[:8]}")
            except Exception:  # noqa: BLE001
                logger.exception("worker poll failed")
            await asyncio.sleep(self._poll_interval_s)

    async def _publish_stats(self) -> None:
        """Keep the queue-depth and oldest-lease gauges current."""
        from opentelemetry.metrics import Observation

        def observe(_options: Any) -> list[Observation]:
            return [Observation(value, {"state": state}) for state, value in self._last_stats.items()]

        instruments().observe_queue(observe)
        while self._running:
            try:
                stats = await self._store.stats()
                self._last_stats = {
                    "pending": stats.pending,
                    "running": stats.running,
                    "suspended": stats.suspended,
                    "dead_letters": stats.dead_letters,
                    "oldest_lease_age_s": stats.oldest_lease_age_s,
                }
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(5.0)

    # ------------------------------------------------------------------ one run

    def _build_tool_invoker(self, agent: Any) -> Any:
        from substrate.kernel.tools.invoker import build_invoker

        return build_invoker(agent)

    def _deadline(self, lease: Lease) -> datetime | None:
        """The run's absolute cutoff: the earlier of its own deadline and its budget's,
        anchored to when the run *first* started — a resumed run must not get a fresh
        allowance on every lease."""
        cutoffs: list[datetime] = []
        if lease.deadline is not None:
            cutoffs.append(lease.deadline)
        budget = lease.supervision.execution_budget if lease.supervision else None
        if budget is not None and budget.deadline_s is not None and lease.started_at is not None:
            cutoffs.append(lease.started_at + timedelta(seconds=budget.deadline_s))
        return min(cutoffs) if cutoffs else None

    async def _run(self, lease: Lease, agent: Any) -> None:
        run_id = lease.run_id
        token = CancellationToken()
        self._tokens[run_id] = token
        started = _now()
        outcome_label = "completed"
        attributes = {
            semconv.RUN_ID: str(run_id),
            semconv.RUN_AGENT: str(lease.agent),
            semconv.RUN_ATTEMPT: lease.attempt,
            semconv.RUN_TENANT: lease.tenant,
            semconv.LEASE_WORKER: self._worker_id,
            semconv.LEASE_EPOCH: lease.epoch,
            semconv.GEN_AI_AGENT_NAME: str(lease.agent),
        }
        if lease.thread_id:
            attributes[semconv.RUN_THREAD] = lease.thread_id
        heartbeat: asyncio.Task[None] | None = None
        hooks = getattr(agent, "hooks", None)
        try:
            with span(semconv.SPAN_RUN, attributes=attributes, parent=lease.trace) as handle:
                try:
                    meta = RunMeta(
                        run_id=run_id,
                        cancellation=token,
                        supervision=lease.supervision,
                        deadline=self._deadline(lease),
                        trace=handle.context(),
                        tenant_id=lease.tenant,
                    )
                    version = str(getattr(agent, "version", "0"))
                    if lease.agent_version != version:
                        raise NonDeterminismError(
                            f"run {run_id} was started by agent version {lease.agent_version!r} but this worker has "
                            f"{version!r}; replaying it could attach old results to new code",
                            path="",
                            expected=lease.agent_version,
                            actual=version,
                        )
                    events = await self._store.read_events(run_id, durable_only=True)

                    async def commit(entries: Any) -> Any:
                        return await self._store.commit(lease, Commit(entries=tuple(entries)))

                    blob_store = getattr(agent, "blob_store", None)
                    journal = Journal(str(run_id), events, commit, blob_store=blob_store)
                    ctx = RunContext(
                        meta=meta,
                        lease=lease,
                        store=self._store,
                        journal=journal,
                        blob_store=blob_store,
                        llm_client=getattr(agent, "model", None),
                        tool_invoker=self._build_tool_invoker(agent),
                        agent=agent,
                    )
                    started_before = any(e.kind == RunLogKind.RUN_STARTED for e in events)
                    await commit(
                        [
                            NewEntry(
                                kind=RunLogKind.RUN_RESUMED if started_before else RunLogKind.RUN_STARTED,
                                payload={"agent": str(lease.agent), "attempt": lease.attempt, "agent_version": version},
                                dedup_key=f"lease:{lease.epoch}",
                            )
                        ]
                    )
                    heartbeat = asyncio.create_task(self._heartbeat(lease, token, asyncio.current_task()), name=f"hb-{run_id[:8]}")

                    drained = await self._journaled_drain(ctx, journal, lease)
                    if hooks:
                        from substrate.kernel.hooks.manager import HookEvent

                        await hooks.dispatch(HookEvent.RUN_START, {"agent_name": str(agent.id), "run_id": run_id})
                    await agent.run(ctx, drained)
                    await self._finish(lease, agent, drained, Complete())
                except SuspendInterrupt as signal:
                    outcome_label = "suspended"
                    await self._commit_end(lease, Commit(outcome=Suspend(wake=signal.wakeup)))
                    instruments().suspensions.add(1)
                except LeaseLostError:
                    outcome_label = "lost"
                    logger.warning("lost the lease on run %s", run_id)
                except (asyncio.CancelledError, CancellationError) as stop:
                    outcome_label = await self._on_stop(lease, agent, stop, token)
                except Exception as exc:  # noqa: BLE001
                    outcome_label = await self._on_failure(lease, agent, exc)
                handle.set_attribute(semconv.RUN_OUTCOME, outcome_label)
        finally:
            if heartbeat is not None:
                heartbeat.cancel()
                try:
                    await heartbeat
                except asyncio.CancelledError:
                    pass
            if hooks and outcome_label != "lost":
                from substrate.kernel.hooks.manager import HookEvent

                await hooks.dispatch(HookEvent.RUN_END, {"agent_name": str(agent.id), "run_id": run_id})
            if outcome_label in ("completed", "failed", "cancelled"):
                instruments().runs.add(1, {semconv.RUN_OUTCOME: outcome_label})
                instruments().run_duration.record((_now() - started).total_seconds(), {semconv.RUN_OUTCOME: outcome_label})
            self._tokens.pop(run_id, None)
            self._tasks.pop(run_id, None)
            self._stop_reason.pop(run_id, None)

    async def _journaled_drain(self, ctx: RunContext, journal: Journal, lease: Lease) -> list[Any]:
        """Drain the inbox once and remember which messages: a replay must process
        exactly the messages the first attempt saw, not whatever has arrived since."""
        fetched: list[Any] = []

        async def build(_path: str, _effect: str):
            fetched.extend(await self._store.drain(lease.agent, limit=100))
            return {"msg_ids": [m.id for m in fetched]}, lambda entries: ctx._commit(entries)

        outcome = await journal.record_atomic("inbox.drain", {}, build)
        if not outcome.replayed:
            return fetched
        by_id = {m.id: m for m in await self._store.drain(lease.agent, limit=1000)}
        return [by_id[i] for i in outcome.value["msg_ids"] if i in by_id]

    async def _heartbeat(self, lease: Lease, token: CancellationToken, task: asyncio.Task[Any] | None) -> None:
        interval = max(self._lease_s / 3, 0.05)
        failures = 0
        run_id = str(lease.run_id)
        while True:
            await asyncio.sleep(interval)
            try:
                result = await self._store.heartbeat(lease, lease_s=self._lease_s, now=_now())
                failures = 0
            except Exception:  # noqa: BLE001
                failures += 1
                logger.warning("heartbeat for run %s failed (%d)", run_id, failures, exc_info=failures == 1)
                if failures < _HEARTBEAT_FAILURES_BEFORE_LOST:
                    continue
                result = HeartbeatResult.LOST
            if result == HeartbeatResult.OK:
                continue
            reason = {
                HeartbeatResult.CANCEL_REQUESTED: "cancel:cancel_requested",
                HeartbeatResult.DEADLINE: "deadline",
                HeartbeatResult.LOST: "lost",
            }[result]
            self._stop_reason[run_id] = reason
            token.cancel(reason)
            if task is not None:
                task.cancel()
            return

    # ------------------------------------------------------------------ ways a run ends

    async def _commit_end(self, lease: Lease, commit: Commit) -> None:
        """Commit how an attempt ended. A lost lease here is not an error: the run is
        someone else's now."""
        try:
            await self._store.commit(lease, commit)
        except LeaseLostError:
            logger.warning("lost the lease on run %s while ending it", lease.run_id)

    async def _finish(self, lease: Lease, agent: Any, handled: list[Any], outcome: Complete) -> None:
        await self._store.commit(lease, Commit(ack=tuple(m.id for m in handled), outcome=outcome))
        await self._clear_run_history(agent, str(lease.run_id), handled)

    async def _on_stop(self, lease: Lease, agent: Any, stop: BaseException, token: CancellationToken) -> str:
        run_id = str(lease.run_id)
        reason = self._stop_reason.get(run_id, "")
        if reason == "lost":
            logger.warning("run %s was stopped because its lease was lost", run_id)
            return "lost"
        if isinstance(stop, asyncio.CancelledError) and reason == "shutdown":
            # Shutting down is not cancelling: leave the run for its lease to expire, and
            # it resumes on whichever worker next picks it up.
            raise stop
        if reason == "deadline" or "deadline" in str(stop):
            error = ErrorInfo(code="deadline_exceeded", message="the run's deadline passed", retryable=False)
            await self._commit_end(lease, Commit(outcome=Fail(error=error)))
            return "failed"
        why = reason.removeprefix("cancel:") or str(stop) or "cancelled"
        await self._commit_end(lease, Commit(outcome=Cancel(reason=why)))
        return "cancelled"

    async def _on_failure(self, lease: Lease, agent: Any, exc: Exception) -> str:
        run_id = str(lease.run_id)
        error, retryable = classify_failure(exc, run_id=run_id, agent_id=agent.id)
        if retryable:
            logger.exception("agent %s crashed in run %s", agent.id, run_id)
        else:
            logger.warning("agent %s run %s stopped: %s", agent.id, run_id, exc)
        inbox = await self._safe_drain(lease)
        if retryable and lease.retry_count < lease.retry_policy.max_retries:
            # The messages stay in the inbox: the retry will find the same input.
            delay = backoff(lease.retry_policy, lease.retry_count)
            await self._commit_end(lease, Commit(outcome=Retry(error=error, delay_s=delay)))
            instruments().retries.add(1)
            return "retrying"
        if retryable:
            # Out of retries: nobody is left to try these messages again.
            nacks = tuple(Nack(msg_id=m.id, error=error, final=True) for m in inbox)
            await self._commit_end(lease, Commit(nack=nacks, outcome=Fail(error=error)))
        else:
            await self._commit_end(lease, Commit(ack=tuple(m.id for m in inbox), outcome=Fail(error=error)))
        return "failed"

    async def _safe_drain(self, lease: Lease) -> list[Any]:
        try:
            return await self._store.drain(lease.agent, limit=100)
        except Exception:  # noqa: BLE001
            return []

    async def _clear_run_history(self, agent: Any, run_id: str, handled: list[Any]) -> None:
        """Delete run-scoped history once the run has ended. After the commit, never
        before: a crash between the two leaks a transcript, whereas deleting first would
        lose one a retry still needs."""
        from substrate.kernel.abstractions.agent.supervision import HistoryRetention

        context_cfg = getattr(agent, "_context", None)
        if context_cfg is None or getattr(context_cfg, "retention", HistoryRetention.PERMANENT) != HistoryRetention.RUN:
            return
        history = getattr(context_cfg, "history", None)
        if history is None:
            return
        for session_id in {m.correlation_id or run_id for m in handled} or {run_id}:
            try:
                await history.delete_session(session_id)
            except Exception:  # noqa: BLE001
                logger.warning("could not delete history for agent %s run %s session %s", agent.id, run_id, session_id)


__all__ = ["Worker", "backoff", "classify_failure"]
