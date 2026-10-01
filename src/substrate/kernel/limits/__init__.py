"""substrate.kernel.limits — the headcount budget for an orchestrator's sub-agent spawns.

Token, cost and turn budgets are not here: they are enforced by the engine itself against the
whole execution tree's spend (``RunContext.llm`` and ``RuntimeStore.tree_spend``), so no agent can
forget to check one and none can get around it by spawning more agents.

Durable, backed-off run retry lives in ``RunRetryPolicy``
(``kernel/abstractions/runtime/scheduler.py``) and the worker's failure handling.
"""

from __future__ import annotations

from substrate.kernel.limits.spawn import SpawnTracker

__all__ = ["SpawnTracker"]
