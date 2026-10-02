"""The crash matrix: fail every durable write, once each, and check what survives.

A run's correctness is decided by what happens when a durable write does not
happen. Ordinary tests never ask, so the windows between recording an effect,
acknowledging a message and ending a run were never exercised — and that is where
a log gets corrupted.

The matrix runs a scenario once to enumerate its writes to the store, then once per
write, failing that one. It does so in two modes, because they are different faults:

``blip``
    The call raises ``InjectedStoreFailure``, an ordinary ``Exception``: a dropped
    connection, a timeout. The worker's own error handling runs.

``death``
    The call raises ``WorkerDied``, a ``BaseException`` nothing catches: the process
    was killed mid-write. No handler runs, no lease is released; the run is only
    recovered when its lease expires and another lease picks it up. This is the fault
    that matters most and the one a handler-based test cannot reach.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

TERMINAL_KINDS = frozenset(
    {"run.completed", "run.failed", "run.cancelled", "run.truncated"}
)
Mode = Literal["blip", "death"]

# Every method that writes to the store.
_WRITES: tuple[str, ...] = (
    "commit",
    "create_run",
    "deliver",
    "signal",
    "request_cancel",
)


class InjectedStoreFailure(Exception):
    """One durable write failed. Models a transient infrastructure fault."""


class WorkerDied(BaseException):
    """The worker process was killed mid-write. Not an ``Exception``: nothing may catch it."""


@dataclass(frozen=True)
class DurableCall:
    target: str
    detail: str

    def __str__(self) -> str:
        return f"{self.target}({self.detail})" if self.detail else self.target


def _describe(method: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    if method == "commit":
        commit = kwargs.get("commit") or (args[1] if len(args) > 1 else None)
        if commit is None:
            return ""
        parts = [e.kind for e in commit.entries]
        if commit.deliveries:
            parts.append(f"+{len(commit.deliveries)} delivery")
        if commit.spawns:
            parts.append("+spawn")
        if commit.ack:
            parts.append(f"+ack{len(commit.ack)}")
        if commit.outcome is not None:
            parts.append(f"=>{commit.outcome.kind}")
        return ",".join(parts) or "empty"
    return ""


class CrashInjector:
    """Records every write to a runtime's store, and fails the ``fail_at``-th."""

    def __init__(
        self, runtime: Any, *, fail_at: int | None = None, mode: Mode = "blip"
    ) -> None:
        self.calls: list[DurableCall] = []
        self.fail_at = fail_at
        self.mode = mode
        self.injected: DurableCall | None = None
        store = runtime.store
        for method in _WRITES:
            original = getattr(store, method)
            setattr(store, method, self._wrap(method, original))

    def _wrap(
        self, method: str, original: Callable[..., Awaitable[Any]]
    ) -> Callable[..., Awaitable[Any]]:
        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            call = DurableCall(method, _describe(method, args, kwargs))
            index = len(self.calls)
            self.calls.append(call)
            if index == self.fail_at:
                self.injected = call
                if self.mode == "death":
                    raise WorkerDied(f"killed at #{index} {call}")
                raise InjectedStoreFailure(f"injected failure at #{index} {call}")
            return await original(*args, **kwargs)

        return wrapped


@dataclass
class Observation:
    """What the world looks like after a scenario settles."""

    terminal: str | None
    log_kinds: list[str]
    side_effects: list[Any]
    calls: list[DurableCall] = field(default_factory=list)
    crashed_at: DurableCall | None = None
    index: int | None = None
    mode: Mode = "blip"
    timed_out: bool = False

    @property
    def terminal_entries(self) -> list[str]:
        return [k for k in self.log_kinds if k in TERMINAL_KINDS]

    def __str__(self) -> str:
        where = f"#{self.index} {self.crashed_at}" if self.crashed_at else "no crash"
        return (
            f"[{self.mode} {where}] terminal={self.terminal} terminal_entries={self.terminal_entries} "
            f"side_effects={len(self.side_effects)}"
            + (" TIMED_OUT" if self.timed_out else "")
        )


async def observe_run(
    runtime: Any, run_id: str, *, timeout: float = 8.0, settle: float = 0.4
) -> tuple[str | None, list[str]]:
    """Wait for a terminal entry, then read the whole durable log.

    Reads on after the first terminal entry on purpose: a duplicated terminal entry is
    one of the things worth catching.
    """

    async def watch() -> str | None:
        async for entry in runtime.tail(run_id):
            if entry.kind in TERMINAL_KINDS:
                return str(entry.kind)
        return None

    terminal: str | None = None
    timed_out = False
    try:
        terminal = await asyncio.wait_for(watch(), timeout=timeout)
    except asyncio.TimeoutError:
        timed_out = True
    await asyncio.sleep(settle)
    kinds = [
        str(e.kind) for e in await runtime.store.read_events(run_id, durable_only=True)
    ]
    if timed_out:
        kinds.append("<timed-out>")
    return terminal, kinds


Scenario = Callable[[int | None, Mode], Awaitable[Observation]]


async def crash_matrix(
    scenario: Scenario, *, mode: Mode = "blip", limit: int | None = None
) -> list[Observation]:
    """Run ``scenario`` clean, then once per write, failing that write."""
    clean = await scenario(None, mode)
    points = len(clean.calls) if limit is None else min(len(clean.calls), limit)
    results = [clean]
    for index in range(points):
        observation = await scenario(index, mode)
        observation.index = index
        results.append(observation)
    return results


def report(observations: list[Observation]) -> str:
    return "\n".join(f"  {o}" for o in observations)


__all__ = [
    "CrashInjector",
    "DurableCall",
    "InjectedStoreFailure",
    "Mode",
    "Observation",
    "Scenario",
    "TERMINAL_KINDS",
    "WorkerDied",
    "crash_matrix",
    "observe_run",
    "report",
]
