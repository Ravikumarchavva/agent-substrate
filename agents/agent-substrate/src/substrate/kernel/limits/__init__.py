"""substrate.agents.limits — enforce numeric run-time budgets, raise on breach.

Two trackers, same shape: ``ExecutionTracker`` (token/cost/turn budget for a
single agent's execution loop) and ``SpawnTracker`` (headcount budget for an
orchestrator's sub-agent spawns). Neither earned a standalone top-level
package on its own.

Durable, backed-off run retry lives in ``RunRetryPolicy``
(``kernel/runtime/scheduler.py``) + ``SchedulerProtocol.release()`` — the real
mechanism the runtime actually exercises on a failed run.
"""

from __future__ import annotations

from substrate.agents.limits.execution import ExecutionTracker
from substrate.agents.limits.spawn import SpawnTracker

__all__ = ["ExecutionTracker", "SpawnTracker"]
