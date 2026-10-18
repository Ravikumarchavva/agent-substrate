"""Worker._deadline — ExecutionBudget.deadline_s wired into the run's absolute cutoff.

Unit-level: the resolution is a pure function of the lease, so it is exercised directly,
without the spawn/orchestration machinery that a supervised run needs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from substrate.types import ExecutionBudget, Supervision
from substrate.types import Actor
from substrate.types import RunId, new_run_id
from substrate.runtime import Lease
from substrate.runtime import ActorResolver
from substrate.runtime import Worker
from substrate.testing.runtime import runtime_store

_AGENT = Actor(type="agent", key="a")


def _worker() -> Worker:
    return Worker(worker_id="w1", store=runtime_store(), resolver=ActorResolver())


def _supervision(run_id: RunId, *, deadline_s: float | None) -> Supervision:
    return Supervision(
        run_id=run_id,
        session_id="s1",
        root_id=Actor(type="agent", key="root"),
        parent_id=None,
        execution_budget=ExecutionBudget(deadline_s=deadline_s),
    )


def _lease(
    *,
    deadline_s: float | None = None,
    started_at: datetime | None = None,
    deadline: datetime | None = None,
    supervised: bool = True,
) -> Lease:
    run_id = new_run_id()
    return Lease(
        run_id=run_id,
        agent=_AGENT,
        worker_id="w1",
        epoch=1,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        supervision=_supervision(run_id, deadline_s=deadline_s) if supervised else None,
        started_at=started_at,
        deadline=deadline,
    )


def test_no_budget_means_no_deadline() -> None:
    assert _worker()._deadline(_lease(supervised=False)) is None
    assert (
        _worker()._deadline(
            _lease(deadline_s=None, started_at=datetime.now(timezone.utc))
        )
        is None
    )


def test_budget_anchors_to_when_the_run_first_started() -> None:
    """A resumed run's deadline must not be pushed out on every re-lease."""
    started_at = datetime.now(timezone.utc) - timedelta(seconds=30)
    deadline = _worker()._deadline(_lease(deadline_s=60.0, started_at=started_at))
    assert deadline == started_at + timedelta(seconds=60)
    assert deadline < datetime.now(timezone.utc) + timedelta(seconds=60)


def test_the_earlier_of_the_run_deadline_and_the_budget_wins() -> None:
    started_at = datetime.now(timezone.utc)
    hard = started_at + timedelta(seconds=10)
    assert (
        _worker()._deadline(
            _lease(deadline_s=60.0, started_at=started_at, deadline=hard)
        )
        == hard
    )
    assert _worker()._deadline(
        _lease(deadline_s=5.0, started_at=started_at, deadline=hard)
    ) == started_at + timedelta(seconds=5)


async def test_run_meta_check_raises_past_deadline() -> None:
    """The other half of the contract: RunMeta.check() enforces the resolved deadline."""
    from substrate.types import RunMeta
    from substrate.types import CancellationError

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
