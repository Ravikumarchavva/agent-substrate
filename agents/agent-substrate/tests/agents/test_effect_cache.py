"""Tests for EffectCache and the EventLog-as-journal replay path.

Covers:
1. EffectCache.fold() — pure reconstruction from EventLog entries.
2. Crash-and-replay — a fresh RunContext, built from a fresh fold() over the
   SAME event log, must not re-execute an already-journaled effect.
3. Artifact offload — large effect values are stored via BlobStore and
   referenced, not inlined; a fresh fold() + lookup dereferences them lazily.
4. Zombie-worker fencing — a stale RunContext's cached seq cursor causes its
   next append to raise ConcurrentAppendError once another writer has moved
   the log forward, instead of silently corrupting it.
"""

from __future__ import annotations

from substrate.agents.runtime.backends._event_log import InMemoryEventLog
from substrate.agents.runtime.backends._fanout import PushAllFanout
from substrate.agents.runtime.backends._follow_graph import InMemoryFollowGraph
from substrate.agents.runtime.backends._inbox import InMemoryInbox
from substrate.agents.runtime.backends._journal import InMemoryJournal
from substrate.agents.runtime.backends._scheduler import InMemoryScheduler
from substrate.agents.runtime.backends._signal_bus import InMemorySignalBus
from substrate.agents.runtime.backends._supervisor import InMemorySupervisor
from substrate.agents.runtime.context import RunContext
from substrate.agents.runtime.effect_cache import EffectCache
from substrate.kernel.agent.runtime_context import CancellationToken, RunMeta
from substrate.kernel.core.errors import ConcurrentAppendError
from substrate.kernel.runtime.effects import EffectResult
from substrate.kernel.runtime.log_entry import RunLogEntry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_ctx(run_id: str, event_log: InMemoryEventLog, *, blob_store=None) -> RunContext:
    """Build a standalone RunContext sharing *event_log*, with a fresh fold.

    Every other collaborator is a fresh in-memory backend — the tests below
    only exercise effect journaling, not inbox/fanout/spawn behavior. Folding
    fresh on every call is the point: it's what makes two RunContexts built
    for the same run_id behave like two independent process lifetimes
    sharing only the durable EventLog (i.e. a crash-and-replay simulation).
    """
    inbox = InMemoryInbox()
    journal = InMemoryJournal()
    scheduler = InMemoryScheduler()
    signal_bus = InMemorySignalBus(scheduler)
    meta = RunMeta(run_id=run_id, cancellation=CancellationToken())
    effect_cache = await EffectCache.fold(event_log, run_id)
    return RunContext(
        meta=meta,
        event_log=event_log,
        effect_cache=effect_cache,
        blob_store=blob_store,
        inbox=inbox,
        follow_graph=InMemoryFollowGraph(),
        fanout=PushAllFanout(),
        scheduler=scheduler,
        supervisor=InMemorySupervisor(event_log, inbox, journal, scheduler, signal_bus),
        signal_bus=signal_bus,
    )


class _InMemoryBlobStore:
    """Minimal BlobStore stub — bytes/text in, ref out, no expiry."""

    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}
        self._n = 0

    async def store(self, data, *, content_type: str = "application/octet-stream") -> str:
        self._n += 1
        ref = f"blob://{self._n}"
        self._blobs[ref] = data.encode() if isinstance(data, str) else data
        return ref

    async def resolve(self, ref: str) -> bytes:
        return self._blobs[ref]

    async def pin(self, ref: str) -> None:
        pass

    async def unpin(self, ref: str) -> None:
        pass


# ---------------------------------------------------------------------------
# 1. EffectCache.fold()
# ---------------------------------------------------------------------------


async def test_fold_empty_log_returns_empty_cache() -> None:
    event_log = InMemoryEventLog()
    cache = await EffectCache.fold(event_log, "run-empty")
    assert cache.last_seq == -1
    assert cache.lookup("anything") is None


async def test_fold_reconstructs_effect_results() -> None:
    event_log = InMemoryEventLog()
    await event_log.append(
        "run-x", RunLogEntry(run_id="run-x", seq=0, kind="run.started"), expected_seq=-1
    )
    await event_log.append(
        "run-x",
        RunLogEntry(
            run_id="run-x",
            seq=1,
            kind="effect.result",
            payload={"effect_id": "eff1", "status": "ok", "value": {"n": 42}},
        ),
        expected_seq=0,
    )

    cache = await EffectCache.fold(event_log, "run-x")
    assert cache.last_seq == 1
    result = cache.lookup("eff1")
    assert result is not None
    assert result.status == "ok"
    assert result.value == {"n": 42}


async def test_fold_preserves_artifact_ref_without_inline_value() -> None:
    event_log = InMemoryEventLog()
    await event_log.append(
        "run-x",
        RunLogEntry(
            run_id="run-x",
            seq=0,
            kind="effect.result",
            payload={"effect_id": "eff1", "status": "ok", "artifact_ref": "blob://abc"},
        ),
        expected_seq=-1,
    )
    cache = await EffectCache.fold(event_log, "run-x")
    result = cache.lookup("eff1")
    assert result is not None
    assert result.artifact_ref == "blob://abc"
    assert result.value == {}


def test_put_updates_cache_immediately() -> None:
    cache = EffectCache(run_id="run-x", effects={}, last_seq=-1)
    cache.put(EffectResult(effect_id="e1", status="ok", value={"a": 1}))
    result = cache.lookup("e1")
    assert result is not None
    assert result.value == {"a": 1}


# ---------------------------------------------------------------------------
# 2. Crash and replay across independent RunContext instances
# ---------------------------------------------------------------------------


async def test_crash_and_replay_does_not_reexecute_journaled_effect() -> None:
    """A fresh RunContext, built from a fresh fold() over the SAME EventLog
    (simulating a brand-new process after a crash), must return the
    already-journaled uuid() value without generating a new one."""
    run_id = "run-crash-replay"
    event_log = InMemoryEventLog()

    ctx1 = await _make_ctx(run_id, event_log)
    first = await ctx1.uuid()

    # Simulate a crash: ctx1 is discarded entirely, a fresh RunContext is
    # built from scratch, sharing only the durable EventLog.
    ctx2 = await _make_ctx(run_id, event_log)
    replayed = await ctx2.uuid()

    assert replayed == first, "replay must return the journaled value, not a fresh one"


async def test_crash_and_replay_llm_effect_does_not_rebill() -> None:
    """Same proof, but for the llm() path — the one that would otherwise
    re-bill an LLM provider on every replay."""
    from substrate.kernel.core.content import TextBlock
    from substrate.kernel.core.usage import Usage
    from substrate.kernel.llm.llm import GenerationOptions, LLMResponse
    from substrate.kernel.messaging.stream import CompletionEvent

    call_count = 0

    class FakeLLMClient:
        model = "fake-model"

        async def generate_stream(self, messages, *, options=GenerationOptions(), ctx=None):
            nonlocal call_count
            call_count += 1
            yield CompletionEvent(
                content=[TextBlock(text="hello")], usage=Usage(input_tokens=1, output_tokens=1)
            )

    run_id = "run-llm-replay"
    event_log = InMemoryEventLog()

    ctx1 = await _make_ctx(run_id, event_log)
    ctx1._llm_client = FakeLLMClient()
    resp1 = await ctx1.llm([])
    assert isinstance(resp1, LLMResponse)
    assert call_count == 1

    ctx2 = await _make_ctx(run_id, event_log)
    ctx2._llm_client = FakeLLMClient()
    resp2 = await ctx2.llm([])

    assert call_count == 1, "replay must not re-invoke the LLM client"
    assert resp2.content[0].text == resp1.content[0].text  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 3. Artifact offload
# ---------------------------------------------------------------------------


async def test_large_effect_value_offloaded_to_blob_store() -> None:
    run_id = "run-offload"
    event_log = InMemoryEventLog()
    blob_store = _InMemoryBlobStore()
    ctx = await _make_ctx(run_id, event_log, blob_store=blob_store)

    big_value = {"data": "x" * 100_000}  # > _ARTIFACT_OFFLOAD_BYTES
    await ctx._record_effect("big-effect", "ok", big_value)

    # The EventLog entry references a blob, not the inline payload.
    entries = [e async for e in event_log.read(run_id)]
    effect_entries = [e for e in entries if e.kind == "effect.result"]
    assert len(effect_entries) == 1
    assert "artifact_ref" in effect_entries[0].payload
    assert "value" not in effect_entries[0].payload
    assert len(blob_store._blobs) == 1


async def test_offloaded_effect_resolves_correctly_on_fresh_fold() -> None:
    """A fresh fold() (no in-memory value, only artifact_ref) must lazily
    dereference the blob when the effect is looked up again."""
    run_id = "run-offload-replay"
    event_log = InMemoryEventLog()
    blob_store = _InMemoryBlobStore()

    ctx1 = await _make_ctx(run_id, event_log, blob_store=blob_store)
    big_value = {"data": "y" * 100_000}
    await ctx1._record_effect("big-effect", "ok", big_value)

    # Fresh process: new ctx, fresh fold — the cache only has artifact_ref.
    ctx2 = await _make_ctx(run_id, event_log, blob_store=blob_store)
    cached = ctx2._lookup_effect("big-effect")
    assert cached is not None
    assert cached.artifact_ref is not None
    assert cached.value == {}  # not inlined in the fold

    resolved = await ctx2._resolve_effect_value(cached)
    assert resolved == big_value


async def test_small_effect_value_stays_inline_not_offloaded() -> None:
    run_id = "run-small"
    event_log = InMemoryEventLog()
    blob_store = _InMemoryBlobStore()
    ctx = await _make_ctx(run_id, event_log, blob_store=blob_store)

    await ctx._record_effect("small-effect", "ok", {"n": 1})

    entries = [e async for e in event_log.read(run_id)]
    effect_entries = [e for e in entries if e.kind == "effect.result"]
    assert effect_entries[0].payload.get("value") == {"n": 1}
    assert "artifact_ref" not in effect_entries[0].payload
    assert len(blob_store._blobs) == 0


# ---------------------------------------------------------------------------
# 4. Zombie-worker fencing
# ---------------------------------------------------------------------------


async def test_stale_context_append_raises_concurrent_append_error() -> None:
    """Two RunContexts for the same run_id (e.g. an old worker's lease was
    reclaimed and a new worker started) — once the fresh one appends, the
    stale one's cached seq cursor is behind reality, and its next _log()
    call must raise ConcurrentAppendError rather than silently racing."""
    run_id = "run-zombie"
    event_log = InMemoryEventLog()

    stale_ctx = await _make_ctx(run_id, event_log)
    fresh_ctx = await _make_ctx(run_id, event_log)

    await fresh_ctx._log("fresh.wrote", {})

    try:
        await stale_ctx._log("stale.wrote", {})
        assert False, "expected ConcurrentAppendError"
    except ConcurrentAppendError:
        pass
