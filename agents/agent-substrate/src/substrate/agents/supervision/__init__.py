"""substrate.agents.supervision — budget tracking and supervision policies.

Durable, backed-off run retry lives in ``RunRetryPolicy``
(``kernel/runtime/scheduler.py``) + ``SchedulerProtocol.release()`` — the real
mechanism the runtime actually exercises on a failed run.
"""

from __future__ import annotations

from .budget import SpawnTracker

__all__ = ["SpawnTracker"]
