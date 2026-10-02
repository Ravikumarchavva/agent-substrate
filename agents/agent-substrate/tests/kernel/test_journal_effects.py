"""The journal's effect record: what a replay serves, re-runs, or refuses.

A ``Journal`` is built from a run's recorded entries, so "a replay" here is simply a
second ``Journal`` built from what the first one committed — the same thing a worker does
when it picks a run up again.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from substrate.types import NonDeterminismError, OrphanedEffectError
from substrate.types import RunLogEntry, RunLogKind
from substrate.runtime import NewEntry
from substrate.runtime.journal import OFFLOAD_BYTES, current_idempotency_key
from substrate.runtime import Journal


class Log:
    """The durable record a journal commits to, and the source of the next journal."""

    def __init__(self) -> None:
        self.entries: list[RunLogEntry] = []
        self.fail_next: int = 0

    async def commit(self, new: Sequence[NewEntry]) -> None:
        if self.fail_next:
            self.fail_next -= 1
            raise ConnectionError("store blip")
        for entry in new:
            self.entries.append(RunLogEntry(run_id="run", seq=len(self.entries), kind=entry.kind, payload=entry.payload))

    def journal(self, blob_store: Any | None = None) -> Journal:
        return Journal("run", list(self.entries), self.commit, blob_store=blob_store)  # type: ignore[arg-type]

    def kinds(self) -> list[str]:
        return [str(e.kind) for e in self.entries]


class Counter:
    """A step body that counts how many times it really ran."""

    def __init__(self, value: dict[str, Any] | None = None) -> None:
        self.runs = 0
        self.keys: list[str | None] = []
        self._value = value if value is not None else {"result": 42}

    async def __call__(self) -> dict[str, Any]:
        self.runs += 1
        self.keys.append(current_idempotency_key())
        return self._value


class InMemoryBlobStore:
    def __init__(self) -> None:
        self.objects: dict[str, str] = {}

    async def store(self, data: str, *, content_type: str) -> str:
        ref = f"blob-{len(self.objects)}"
        self.objects[ref] = data
        return ref

    async def pin(self, ref: str) -> None:
        return None

    async def resolve(self, ref: str) -> str:
        return self.objects[ref]


async def test_a_new_effect_records_its_intent_before_its_result() -> None:
    log = Log()
    out = await log.journal().effect("tool", {"n": 1}, Counter(), idempotent=False)

    assert out.replayed is False
    assert log.kinds() == [RunLogKind.EFFECT_INTENT, RunLogKind.EFFECT_RESULT]


async def test_a_replay_serves_a_completed_effect_without_running_it() -> None:
    log = Log()
    first = Counter()
    await log.journal().effect("tool", {"n": 1}, first, idempotent=False)

    replay = Counter()
    out = await log.journal().effect("tool", {"n": 1}, replay, idempotent=False)

    assert out.replayed is True and out.value == {"result": 42}
    assert replay.runs == 0, "a completed effect must never run twice"


async def test_a_failed_effect_is_re_executed_on_replay_not_served_from_the_record() -> None:
    """A recorded failure must not be replayed forever — that is what made a retry
    re-raise the same cached error until retries ran out."""
    log = Log()

    async def boom() -> dict[str, Any]:
        raise RuntimeError("transient")

    with pytest.raises(RuntimeError):
        await log.journal().effect("tool", {"n": 1}, boom, idempotent=False)

    retry = Counter()
    out = await log.journal().effect("tool", {"n": 1}, retry, idempotent=False)

    assert retry.runs == 1 and out.replayed is False


async def test_an_error_then_a_success_leaves_the_success_in_the_record() -> None:
    log = Log()

    async def boom() -> dict[str, Any]:
        raise RuntimeError("transient")

    with pytest.raises(RuntimeError):
        await log.journal().effect("tool", {"n": 1}, boom, idempotent=False)
    await log.journal().effect("tool", {"n": 1}, Counter({"result": "done"}), idempotent=False)

    third = Counter()
    out = await log.journal().effect("tool", {"n": 1}, third, idempotent=False)
    assert out.replayed is True and out.value == {"result": "done"} and third.runs == 0


async def test_the_same_path_with_different_arguments_is_a_loud_divergence() -> None:
    """Identity is path + kind + argument digest: changed values must not silently reuse a
    stale result."""
    log = Log()
    await log.journal().effect("tool", {"amount": 100}, Counter(), idempotent=False)

    with pytest.raises(NonDeterminismError):
        await log.journal().effect("tool", {"amount": 999}, Counter(), idempotent=False)


async def test_the_same_path_with_a_different_kind_is_a_loud_divergence() -> None:
    log = Log()
    await log.journal().effect("tool", {"n": 1}, Counter(), idempotent=False)

    with pytest.raises(NonDeterminismError):
        await log.journal().effect("llm", {"n": 1}, Counter(), idempotent=False)


async def test_an_orphaned_effect_that_is_not_idempotent_is_never_run_again() -> None:
    """Started, outcome never recorded: nothing can say whether it took effect."""
    log = Log()
    journal = log.journal()
    body = Counter()
    # Die between the intent and the outcome: the body runs, then every attempt to record fails.
    original = log.commit

    async def die_on_outcome(new: Sequence[NewEntry]) -> None:
        if any(e.kind == RunLogKind.EFFECT_RESULT for e in new):
            raise ConnectionError("store down")
        await original(new)

    journal._commit = die_on_outcome  # type: ignore[assignment]
    with pytest.raises(ConnectionError):
        await journal.effect("tool", {"n": 1}, body, idempotent=False)
    assert body.runs == 1

    rerun = Counter()
    with pytest.raises(OrphanedEffectError):
        await log.journal().effect("tool", {"n": 1}, rerun, idempotent=False)
    assert rerun.runs == 0


async def test_an_orphaned_idempotent_effect_re_runs_under_the_same_key() -> None:
    log = Log()
    original = log.commit

    async def die_on_outcome(new: Sequence[NewEntry]) -> None:
        if any(e.kind == RunLogKind.EFFECT_RESULT for e in new):
            raise ConnectionError("store down")
        await original(new)

    journal = log.journal()
    journal._commit = die_on_outcome  # type: ignore[assignment]
    first = Counter()
    with pytest.raises(ConnectionError):
        await journal.effect("tool", {"n": 1}, first, idempotent=True)

    second = Counter()
    await log.journal().effect("tool", {"n": 1}, second, idempotent=True)

    assert second.runs == 1
    assert second.keys == first.keys and first.keys[0] is not None, "the retry must carry the same idempotency key"


async def test_a_transient_blip_recording_the_outcome_does_not_lose_the_effect() -> None:
    """The work is done; only its record failed. Retrying the record is what stops the
    step being reported as failed (and re-run)."""
    log = Log()
    journal = log.journal()
    body = Counter()
    original = log.commit
    seen = {"results": 0}

    async def blip_once(new: Sequence[NewEntry]) -> None:
        if any(e.kind == RunLogKind.EFFECT_RESULT for e in new):
            seen["results"] += 1
            if seen["results"] == 1:
                raise ConnectionError("blip")
        await original(new)

    journal._commit = blip_once  # type: ignore[assignment]
    out = await journal.effect("tool", {"n": 1}, body, idempotent=False)

    assert out.value == {"result": 42} and body.runs == 1
    assert RunLogKind.EFFECT_RESULT in log.kinds()


async def test_a_large_value_is_offloaded_and_resolves_on_a_fresh_journal() -> None:
    log = Log()
    blobs = InMemoryBlobStore()
    big = {"text": "x" * (OFFLOAD_BYTES + 10)}
    await log.journal(blobs).effect("tool", {"n": 1}, Counter(big), idempotent=False)

    result = next(e for e in log.entries if e.kind == RunLogKind.EFFECT_RESULT)
    assert "value" not in result.payload and result.payload["artifact_ref"] in blobs.objects

    replay = Counter()
    out = await log.journal(blobs).effect("tool", {"n": 1}, replay, idempotent=False)
    assert out.value == big and replay.runs == 0


async def test_a_small_value_stays_inline() -> None:
    log = Log()
    blobs = InMemoryBlobStore()
    await log.journal(blobs).effect("tool", {"n": 1}, Counter({"ok": True}), idempotent=False)

    result = next(e for e in log.entries if e.kind == RunLogKind.EFFECT_RESULT)
    assert result.payload["value"] == {"ok": True} and not blobs.objects
