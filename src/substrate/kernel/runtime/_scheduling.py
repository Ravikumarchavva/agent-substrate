"""SchedulerBackend — the real DI contract for "a scheduler" in this codebase.

``SchedulerProtocol`` and ``RunRegistryProtocol`` (``kernel/runtime/scheduler.py``)
are deliberately two Protocols — queue/lease mechanics vs. lookup/registry —
per that module's own docstring: "split for the type each caller depends
on, not for a second concrete class. Every real implementation still
implements both."

That's the catch: every *site* in this codebase that depends on "a
scheduler" actually calls methods from both Protocols on the same object,
but was typing its dependency against ``SchedulerProtocol`` alone (or a
concrete class) — which type-checks clean today because every real
implementation happens to satisfy both, and then breaks at runtime with a
future implementation that only satisfies one. ``SchedulerBackend`` is the
combined contract those call sites actually need; a caller that genuinely
only needs queue mechanics, or genuinely only needs lookups, should keep
depending on the narrower Protocol instead of this one.

It also declares three sync methods — ``register_run``/``agent_for``/
``wakeup_for`` — that every real backend (``InMemoryScheduler``,
``LocalScheduler``, ``integrations/runtime/scheduler.py::Scheduler``)
implements identically, but that live on neither kernel Protocol. They're
an in-process convenience (cheap synchronous registry reads/writes used by
``Runtime``/``Supervisor``/``SignalBus`` internals, not part of the
across-the-wire durable contract), not a formal kernel guarantee — but
since every implementation agrees on them today, this Protocol documents
the real, complete dependency rather than leaving it undeclared and
duck-typed.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from substrate.kernel.core.identity import Actor
from substrate.kernel.runtime.ids import RunId
from substrate.kernel.runtime.scheduler import RunRegistryProtocol, SchedulerProtocol
from substrate.kernel.runtime.wakeup import Wakeup


@runtime_checkable
class SchedulerBackend(SchedulerProtocol, RunRegistryProtocol, Protocol):
    """A scheduler that provides queue mechanics, registry lookups, and the
    three sync in-process-registry methods every real backend implements —
    see module docstring for why those three aren't on a kernel Protocol."""

    def register_run(self, run_id: RunId, agent_id: Actor) -> None: ...
    def agent_for(self, run_id: RunId) -> Actor | None: ...
    def wakeup_for(self, run_id: RunId) -> Wakeup | None: ...


__all__ = ["SchedulerBackend"]
