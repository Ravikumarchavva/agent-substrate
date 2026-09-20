"""Worker._resolve_deadline — ExecutionBudget.deadline_s wired into a real RunMeta.deadline.

Unit-level: exercises the resolution logic directly against a real
InMemoryEventLog, without needing the full spawn/orchestration machinery
(Supervision.supervision_of() only returns non-None for spawned runs).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from substrate.agents.runtime.backends._event_log import InMemoryEventLog
from substrate.agents.runtime.worker import Worker
from substrate.kernel.agent.supervision import ExecutionBudget, Supervision
from substrate.kernel.core.identity import Actor
from substrate.kernel.runtime.ids import RunId, new_run_id
from substrate.kernel.runtime.log_entry import RunLogEntry, RunLogKind


def _worker(event_log: InMemoryEventLog) -> Worker:
    # Only self._event_log is touched by _resolve_deadline — the rest are
    # never called, so None stand-ins are fine for this unit-level test.
    return Worker(
        worker_id="w1",
        event_log=event_log,
        inbox=None,  # type: ignore[arg-type]
        follow_graph=None,  # type: ignore[arg-type]
        fanout=None,  # type: ignore[arg-type]
        scheduler=None,  # type: ignore[arg-type]
        supervisor=None,  # type: ignore[arg-type]
        signal_bus=None,  # type: ignore[arg-type]
        resolver=None,  # type: ignore[arg-type]
    )


def _supervision(run_id: RunId, *, deadline_s: float | None) -> Supervision:
    return Supervision(
        run_id=run_id,
        session_id="s1",
        root_id=Actor(type="agent", key="root"),
        parent_id=None,
        execution_budget=ExecutionBudget(deadline_s=deadline_s),
    )


async def test_no_budget_means_no_deadline() -> None:
    event_log = InMemoryEventLog()
    worker = _worker(event_log)
    run_id = new_run_id()
    assert await worker._resolve_deadline(run_id, None) is None
    assert await worker._resolve_deadline(run_id, _supervision(run_id, deadline_s=None)) is None


async def test_fresh_run_anchors_deadline_to_now() -> None:
    """First lease, empty log: "now" genuinely is the run's start."""
    event_log = InMemoryEventLog()
    worker = _worker(event_log)
    run_id = new_run_id()
    before = datetime.now(timezone.utc)
    deadline = await worker._resolve_deadline(run_id, _supervision(run_id, deadline_s=60.0))
    after = datetime.now(timezone.utc)
    assert deadline is not None
    assert before + timedelta(seconds=60) <= deadline <= after + timedelta(seconds=60)


async def test_resumed_run_anchors_to_original_start_not_now() -> None:
    """A resumed run's deadline must not be pushed out on every re-lease."""
    event_log = InMemoryEventLog()
    run_id = new_run_id()
    started_at = datetime.now(timezone.utc) - timedelta(seconds=30)
    await event_log.append(
        run_id,
        RunLogEntry(run_id=run_id, seq=0, kind=RunLogKind.RUN_STARTED, ts=started_at),
        expected_seq=-1,
    )
    worker = _worker(event_log)

    deadline = await worker._resolve_deadline(run_id, _supervision(run_id, deadline_s=60.0))

    assert deadline is not None
    # Anchored to started_at (30s ago) + 60s, NOT datetime.now() + 60s.
    assert abs((deadline - (started_at + timedelta(seconds=60))).total_seconds()) < 1
    assert deadline < datetime.now(timezone.utc) + timedelta(seconds=60)


async def test_run_meta_check_raises_past_deadline() -> None:
    """The other half of the contract: RunMeta.check() enforces the resolved deadline."""
    from substrate.kernel.agent.runtime_context import RunMeta
    from substrate.kernel.exceptions import CancellationError

    class _NeverCancelledToken:
        is_cancelled = False

        def check(self) -> None:
            return None

    meta = RunMeta(
        run_id="r1",
        cancellation=_NeverCancelledToken(),  # type: ignore[arg-type]
        deadline=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    with pytest.raises(CancellationError):
        meta.check()
