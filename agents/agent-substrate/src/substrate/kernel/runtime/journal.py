"""The journal — how a run remembers what it has already done.

A run is replayed from the top whenever it resumes, so every step with a side
effect has to be recognised as "already done" and answered from the record
instead of being done again. This module is that mechanism, and it is the part of
the engine whose mistakes are most expensive: a lost record re-bills an LLM call or
re-sends an email.

Identity
--------
A step is identified by its *position*: ``path`` is "the Nth journaled call in this
scope", hierarchical so that a call that is answered from the record (and so never
runs its body) still consumes exactly one index in its parent's scope, leaving
every later sibling where it was. Together with the step's ``kind`` that gives an
``effect_id``. The step's *arguments* are not part of the id; they are a ``digest``
stored alongside. A replay that reaches a path holding a different kind or digest
has diverged from its record — the agent's code changed, or it is not deterministic
— and executing on would attach a stale result to a different call, so it raises
``NonDeterminismError`` before doing anything.

Three states, not two
---------------------
``effect.intent`` is committed *before* a step runs and ``effect.result`` after. A
fold therefore sees each step as one of:

* **completed** — answer from the record.
* **absent** — never started, or failed, or was interrupted by a suspension:
  run it.
* **orphaned** — intent with no outcome. The worker died, or the outcome could not
  be recorded, so it is unknown whether the step happened. A step that declared
  itself idempotent is run again under the same ``idempotency_key``; any other is
  not run, and the run fails with ``OrphanedEffectError`` so a person can decide.
  Silently re-running it is how a card gets charged twice.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, TypeVar

from substrate.kernel.abstractions.core.content import JsonObject
from substrate.kernel.abstractions.core.error_info import ErrorInfo
from substrate.kernel.abstractions.exceptions import (
    ControlSignal,
    KernelError,
    NonDeterminismError,
    OrphanedEffectError,
)
from substrate.kernel.abstractions.runtime.effects import Effect, args_digest
from substrate.kernel.abstractions.runtime.log_entry import RunLogEntry, RunLogKind
from substrate.kernel.abstractions.runtime.store import NewEntry
from substrate.kernel.telemetry import instruments

T = TypeVar("T")

# Values serialised larger than this are offloaded to a blob store and the journal
# keeps only the reference, so one large tool result does not bloat every replay.
OFFLOAD_BYTES = 64 * 1024
# The outcome of a step that already ran must not be lost to a blip in the store:
# the work is done, only the record of it is missing. A few quick retries make a
# transient failure invisible; a persistent one still surfaces as an orphan.
_OUTCOME_ATTEMPTS = 3
_OUTCOME_BACKOFF_S = 0.05

_current_effect: ContextVar[str | None] = ContextVar("substrate_current_effect", default=None)
# Concurrent journaled calls (a tool batch) each need their own path stack. Keyed by
# the journal so an unrelated run's task never picks it up.
_scope_override: ContextVar[tuple[int, list[int]] | None] = ContextVar("substrate_scope_override", default=None)

Commit = Callable[[Sequence[NewEntry]], Awaitable[Any]]


def current_idempotency_key() -> str | None:
    """The idempotency key of the journaled step now executing, if any."""
    return _current_effect.get()


@dataclass(frozen=True)
class Outcome:
    """The result of a journaled step."""

    value: JsonObject
    replayed: bool
    effect_id: str


@dataclass
class _Known:
    """What the record says about one effect id."""

    state: str  # "completed" | "pending" | "absent"
    kind: str = ""
    digest: str = ""
    value: JsonObject | None = None
    artifact_ref: str | None = None


class Journal:
    """Path allocation and the effect record for one lease of one run."""

    def __init__(
        self,
        run_id: str,
        entries: Sequence[RunLogEntry],
        commit: Commit,
        *,
        blob_store: Any | None = None,
    ) -> None:
        self.run_id = run_id
        self._commit = commit
        self._blob = blob_store
        self._path_stack: list[int] = [0]
        self._known: dict[str, _Known] = {}
        self._by_path: dict[str, tuple[str, str]] = {}  # path -> (kind, digest)
        for entry in entries:
            self._fold(entry)

    # ------------------------------------------------------------------ fold

    def _fold(self, entry: RunLogEntry) -> None:
        payload = entry.payload
        if entry.kind == RunLogKind.EFFECT_INTENT:
            effect_id = payload["effect_id"]
            self._by_path[payload["path"]] = (payload["kind"], payload["digest"])
            current = self._known.get(effect_id)
            if current is None or current.state != "completed":
                self._known[effect_id] = _Known("pending", payload["kind"], payload["digest"])
        elif entry.kind == RunLogKind.EFFECT_RESULT:
            effect_id = payload["effect_id"]
            if payload["status"] == "ok":
                known = self._known.get(effect_id)
                self._known[effect_id] = _Known(
                    "completed",
                    known.kind if known else payload.get("kind", ""),
                    known.digest if known else payload.get("digest", ""),
                    value=payload.get("value") or {},
                    artifact_ref=payload.get("artifact_ref"),
                )
                if "path" in payload:
                    self._by_path[payload["path"]] = (payload.get("kind", ""), payload.get("digest", ""))
            else:
                # Failed or interrupted: not done, and not in doubt. Run it again.
                self._known[effect_id] = _Known("absent")

    @property
    def completed(self) -> int:
        return sum(1 for k in self._known.values() if k.state == "completed")

    # ------------------------------------------------------------------ paths

    def alloc_path(self) -> str:
        """This call's path in the current scope.

        Always consumes exactly one index, whether the call turns out to be answered
        from the record or executed. That symmetry is what keeps later siblings
        aligned between a live run and every replay.
        """
        stack = self._stack()
        path = ".".join(str(i) for i in stack)
        stack[-1] += 1
        return path

    def peek_path(self) -> str:
        """The path the next journaled call will get, without consuming it. For a step
        that needs its own position in an identifier it builds before it runs."""
        return ".".join(str(i) for i in self._stack())

    def _stack(self) -> list[int]:
        override = _scope_override.get()
        if override is not None and override[0] == id(self):
            return override[1]
        return self._path_stack

    def fork_scopes(self, n: int) -> list[list[int]]:
        """Stacks for ``n`` calls about to run concurrently: call ``i`` gets exactly
        the path it would have had if the calls ran one after another."""
        base = self._stack()
        stacks = [[*base[:-1], base[-1] + i] for i in range(n)]
        base[-1] += n
        return stacks

    async def in_scope(self, stack: list[int], fn: Callable[[], Awaitable[T]]) -> T:
        token = _scope_override.set((id(self), stack))
        try:
            return await fn()
        finally:
            _scope_override.reset(token)

    def enter_scope(self) -> None:
        """Open a child scope for calls made inside a step's body. Only on the
        execution path: an answered-from-record step never enters it, because its
        body does not run."""
        self._stack().append(0)

    def exit_scope(self) -> None:
        self._stack().pop()

    # ------------------------------------------------------------------ steps

    def _identify(self, kind: str, args: Any) -> tuple[str, str, str]:
        path = self.alloc_path()
        digest = args_digest(args)
        prior = self._by_path.get(path)
        if prior is not None and prior != (kind, digest):
            raise NonDeterminismError(
                f"replay diverged from the journal at path {path!r}: recorded {prior[0]!r}, "
                f"now {kind!r}" + (" with different arguments" if prior[0] == kind else ""),
                path=path,
                expected=f"{prior[0]}:{prior[1]}",
                actual=f"{kind}:{digest}",
            )
        return path, Effect.make_id(self.run_id, path, kind, {}), digest

    async def _resolve(self, known: _Known) -> JsonObject:
        """The recorded value, fetched from the blob store if it was offloaded."""
        if known.artifact_ref and not known.value:
            if self._blob is None:
                raise RuntimeError(
                    f"journaled value is offloaded ({known.artifact_ref}) but no blob store is configured"
                )
            raw = await self._blob.resolve(known.artifact_ref)
            return json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
        return known.value or {}

    async def _offload(self, value: JsonObject) -> tuple[JsonObject, str | None]:
        if self._blob is None:
            return value, None
        text = json.dumps(value, default=str)
        if len(text.encode()) <= OFFLOAD_BYTES:
            return value, None
        ref = await self._blob.store(text, content_type="application/json")
        await self._blob.pin(ref)
        return {}, ref

    async def _record(self, entries: Sequence[NewEntry]) -> None:
        """Commit an outcome, retrying a transient store failure: the step already ran."""
        last: BaseException | None = None
        for attempt in range(_OUTCOME_ATTEMPTS):
            try:
                await self._commit(entries)
                return
            except ControlSignal:
                raise
            except Exception as exc:  # noqa: BLE001 - any store failure is retried, then surfaced
                last = exc
                if attempt + 1 < _OUTCOME_ATTEMPTS:
                    await asyncio.sleep(_OUTCOME_BACKOFF_S * (attempt + 1))
        assert last is not None
        raise last

    async def effect(
        self,
        kind: str,
        args: Any,
        fn: Callable[[], Awaitable[JsonObject]],
        *,
        idempotent: bool,
    ) -> Outcome:
        """Run ``fn`` once across all replays.

        ``fn`` returns the JSON-able value to record; it runs under the step's
        ``idempotency_key`` (see ``current_idempotency_key``).
        """
        path, effect_id, digest = self._identify(kind, args)
        known = self._known.get(effect_id)
        if known is not None and known.state == "completed":
            instruments().replay_hits.add(1, {"kind": kind})
            return Outcome(await self._resolve(known), True, effect_id)
        if known is not None and known.state == "pending" and not idempotent:
            raise OrphanedEffectError(
                f"{kind} at path {path!r} was started and its outcome was never recorded; it is not "
                "marked idempotent, so it will not be run again. Check whether it took effect.",
                effect_id=effect_id,
                kind=kind,
            )
        self.enter_scope()
        try:
            if known is None or known.state != "pending":
                await self._commit(
                    [
                        NewEntry(
                            kind=RunLogKind.EFFECT_INTENT,
                            payload={
                                "effect_id": effect_id,
                                "path": path,
                                "kind": kind,
                                "digest": digest,
                                "idempotent": idempotent,
                            },
                        )
                    ]
                )
                self._known[effect_id] = _Known("pending", kind, digest)
            token = _current_effect.set(effect_id)
            try:
                value = await fn()
            except ControlSignal:
                # Suspension or cancellation interrupts a step; it neither completed nor
                # is in doubt. Marking it so lets the resumed run execute it afresh.
                await self._abort(effect_id, "interrupted")
                raise
            except Exception as exc:
                await self._fail(effect_id, exc)
                raise
            finally:
                _current_effect.reset(token)
            stored, ref = await self._offload(value)
            payload: JsonObject = {
                "effect_id": effect_id,
                "path": path,
                "kind": kind,
                "digest": digest,
                "status": "ok",
            }
            if ref:
                payload["artifact_ref"] = ref
            else:
                payload["value"] = stored
            await self._record([NewEntry(kind=RunLogKind.EFFECT_RESULT, payload=payload)])
            self._known[effect_id] = _Known("completed", kind, digest, value=value, artifact_ref=None)
            return Outcome(value, False, effect_id)
        finally:
            self.exit_scope()

    async def _abort(self, effect_id: str, reason: str) -> None:
        self._known[effect_id] = _Known("absent")
        try:
            await self._record(
                [NewEntry(kind=RunLogKind.EFFECT_RESULT, payload={"effect_id": effect_id, "status": "aborted", "reason": reason})]
            )
        except Exception:  # noqa: BLE001 - the interruption itself must not be masked
            pass

    async def _fail(self, effect_id: str, exc: BaseException) -> None:
        self._known[effect_id] = _Known("absent")
        info = exc.to_info() if isinstance(exc, KernelError) else ErrorInfo(code="effect_failed", message=str(exc)[:500])
        try:
            await self._record(
                [
                    NewEntry(
                        kind=RunLogKind.EFFECT_RESULT,
                        payload={"effect_id": effect_id, "status": "error", "error": info.model_dump(mode="json")},
                    )
                ]
            )
        except Exception:  # noqa: BLE001 - report the original failure, not the bookkeeping
            pass

    async def record_atomic(
        self,
        kind: str,
        args: Any,
        build: Callable[[str, str], Any],
    ) -> Outcome:
        """A step whose execution *is* a store commit (sending a message, spawning a
        child, reading the clock). The outcome is written in that same commit, so
        there is no window between doing it and recording it — and so no intent.

        ``build(path, effect_id)`` — sync or async — returns ``(value, do_commit)``;
        ``do_commit(entries)`` must commit those entries together with whatever the
        step does. It runs only when the step is not already recorded.
        """
        path, effect_id, digest = self._identify(kind, args)
        known = self._known.get(effect_id)
        if known is not None and known.state == "completed":
            instruments().replay_hits.add(1, {"kind": kind})
            return Outcome(await self._resolve(known), True, effect_id)
        built = build(path, effect_id)
        value, do_commit = await built if inspect.isawaitable(built) else built
        outcome = NewEntry(
            kind=RunLogKind.EFFECT_RESULT,
            payload={"effect_id": effect_id, "path": path, "kind": kind, "digest": digest, "status": "ok", "value": value},
            dedup_key=f"effect:{effect_id}",
        )
        await do_commit([outcome])
        self._known[effect_id] = _Known("completed", kind, digest, value=value)
        return Outcome(value, False, effect_id)

    def peek(self, kind: str, args: Any) -> tuple[str, str, JsonObject | None]:
        """Identify a step and report whether it is already recorded, without executing it.

        Returns ``(path, effect_id, value_or_None)``. Used by waits, which re-check on
        every replay rather than record a result.
        """
        path, effect_id, _ = self._identify(kind, args)
        known = self._known.get(effect_id)
        value = known.value if known is not None and known.state == "completed" else None
        return path, effect_id, value


__all__ = ["Journal", "OFFLOAD_BYTES", "Outcome", "current_idempotency_key"]
