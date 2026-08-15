"""RunContext journaling mixin — effect-path allocation, EventLogProtocol append, effect cache.

Split out of ``context/__init__.py`` (see that module's docstring for the full
suspend/resume/replay contract this all serves). Everything here is
self-contained: no calls into the other ``RunContext`` mixins.
"""

from __future__ import annotations

import json
import random as _random
import uuid as _uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Literal

from substrate.kernel.core.content import JsonObject
from substrate.kernel.runtime.effects import Effect, EffectResult
from substrate.kernel.runtime.log_entry import RunLogEntry

if TYPE_CHECKING:
    from substrate.kernel.runtime.log_entry import EventLogProtocol
    from substrate.kernel.storage.blob import BlobStore
    from substrate.agents.runtime.effect_cache import EffectCache

# Effect results serialized larger than this are offloaded to the BlobStore
# and referenced by ``artifact_ref`` rather than inlined in the EventLogProtocol
# entry — keeps large tool/LLM payloads out of the hot append-only log.
_ARTIFACT_OFFLOAD_BYTES = 64 * 1024


class _JournalMixin:
    """Hierarchical effect-path allocation + EventLogProtocol/EffectCache journaling."""

    if TYPE_CHECKING:
        run_id: str
        _path_stack: list[int]
        _effect_cache: EffectCache
        _blob_store: BlobStore | None
        _event_log: EventLogProtocol
        _seq_cursor: int

    # ------------------------------------------------------------------
    # Hierarchical effect-path allocation (see RunContext docstring for why)
    # ------------------------------------------------------------------

    def _alloc_path(self) -> str:
        """Allocate this call's own path in the current scope.

        Always consumes exactly one index in the current scope — on both the
        live run and every replay — regardless of whether the call turns out
        to be a journal hit or miss. That symmetry is what keeps sibling
        calls after this one aligned between live execution and replay.
        """
        path = ".".join(str(i) for i in self._path_stack)
        self._path_stack[-1] += 1
        return path

    def _enter_scope(self) -> None:
        """Open a child scope for calls made inside a journaled call's body.

        Only call this on the genuine-execution (journal miss) path — a
        cache-hit call must never enter its scope, since its body (and
        anything it would have journaled) does not run.
        """
        self._path_stack.append(0)

    def _exit_scope(self) -> None:
        self._path_stack.pop()

    # ------------------------------------------------------------------
    # Journaled generic effect helper
    # ------------------------------------------------------------------

    async def _journaled(
        self,
        kind: str,
        args: JsonObject,
        fn: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Run fn() with at-most-once semantics via the effect cache.

        ``fn`` may itself make journaled calls — a child scope is opened for
        the duration of its (genuine) execution so any such calls get stable,
        replay-safe paths of their own.
        """
        path = self._alloc_path()
        effect_id = Effect.make_id(self.run_id, path, kind, args)
        cached = self._lookup_effect(effect_id)
        if cached:
            if cached.status == "error":
                value = await self._resolve_effect_value(cached)
                raise RuntimeError(value.get("error", "journaled error"))
            return await self._resolve_effect_value(cached)
        self._enter_scope()
        try:
            result = await fn()
            await self._record_effect(effect_id, "ok", result or {})
            return result
        except Exception as exc:
            await self._record_effect(effect_id, "error", {"error": str(exc)})
            raise
        finally:
            self._exit_scope()

    async def _log(self, kind: str, payload: JsonObject = {}) -> int:
        seq = self._seq_cursor + 1
        await self._event_log.append(
            self.run_id,
            RunLogEntry(run_id=self.run_id, seq=seq, kind=kind, payload=payload),
            expected_seq=self._seq_cursor,
        )
        self._seq_cursor = seq
        return seq

    async def log_once(self, kind: str, payload: JsonObject | None = None) -> int:
        """Journaled EventLogProtocol append — happens at most once across all replay
        attempts, unlike plain ``_log`` (which appends unconditionally on
        every call).

        Needed for any informational entry inside a tool body that can
        suspend via ``SuspendInterrupt``: the outer ``tool()`` wrapper's own
        effect is deliberately never recorded before a suspend
        (``SuspendInterrupt`` is a ``BaseException`` specifically so it
        bypasses the ``except Exception`` that would otherwise record it —
        see ``tool()``), so the entire tool body — including everything
        before the suspend point — re-executes on every resume. A plain
        ``_log`` call in that path (e.g. ``ask_human``'s
        ``input.requested``) would append a duplicate entry, and a
        duplicate UI card, once per suspend/resume cycle. This doesn't:
        the first attempt logs it and records a marker effect; every
        subsequent replay hits that marker and skips the append entirely.

        Returns the entry's seq either way — on a fresh append that's the
        seq `_log` just assigned; on a replay-skip, `self._seq_cursor` is
        unchanged by this call and already correctly reflects that earlier
        entry's position (it's restored from `effect_cache.last_seq` at
        `RunContext` construction, so it accounts for entries from a prior
        attempt too) — correct in both cases without an extra lookup, valid
        for any caller where nothing else appends between this call and
        reading the return value (true for `log_user_message`, its only
        current caller needing the seq).
        """
        path = self._alloc_path()
        effect_id = Effect.make_id(self.run_id, path, "log_once", {"kind": kind})
        if self._lookup_effect(effect_id) is not None:
            return self._seq_cursor
        seq = await self._log(kind, payload or {})
        await self._record_effect(effect_id, "ok", {})
        return seq

    # ------------------------------------------------------------------
    # Effect cache — lookup/record against the EventLogProtocol (replaces Journal)
    # ------------------------------------------------------------------

    def _lookup_effect(self, effect_id: str) -> EffectResult | None:
        """Pure in-memory lookup — no I/O. Value may be offloaded; see ``_resolve_effect_value``."""
        return self._effect_cache.lookup(effect_id)

    async def _resolve_effect_value(self, result: EffectResult) -> JsonObject:
        """Dereference an offloaded effect value.  A no-op unless the cached
        result came from a fold() of an entry whose value was too large to
        inline (see ``_record_effect``) — the live-write path always keeps
        the value in memory too, so this only does blob I/O on a genuine
        cross-process replay hitting an offloaded historical effect."""
        if result.artifact_ref and not result.value:
            if self._blob_store is None:
                raise RuntimeError(
                    f"Effect {result.effect_id} references an offloaded artifact "
                    f"({result.artifact_ref}) but no blob_store is configured"
                )
            raw = await self._blob_store.resolve(result.artifact_ref)
            text = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
            return json.loads(text)
        return result.value

    async def _record_effect(
        self, effect_id: str, status: Literal["ok", "error"], value: JsonObject
    ) -> None:
        """Append ``effect.result`` to the EventLogProtocol (durable, replay source of
        truth) and update the in-run cache.  Large values are offloaded to
        the BlobStore and referenced rather than inlined in the log entry."""
        payload: JsonObject = {"effect_id": effect_id, "status": status}
        artifact_ref: str | None = None
        serialized = json.dumps(value, default=str)
        if (
            self._blob_store is not None
            and len(serialized.encode()) > _ARTIFACT_OFFLOAD_BYTES
        ):
            artifact_ref = await self._blob_store.store(
                serialized, content_type="application/json"
            )
            await self._blob_store.pin(artifact_ref)
            payload["artifact_ref"] = artifact_ref
        else:
            payload["value"] = value
        await self._log("effect.result", payload)
        # Keep the full value in the in-memory cache regardless of offload —
        # we already have it live; only a fresh fold() ever needs to resolve
        # the blob.
        self._effect_cache.put(
            EffectResult(
                effect_id=effect_id,
                status=status,
                value=value,
                artifact_ref=artifact_ref,
            )
        )

    # ------------------------------------------------------------------
    # Deterministic helpers (for replay safety)
    # ------------------------------------------------------------------

    async def now(self) -> datetime:  # type: ignore[override]
        """Journaled wall-clock — use this instead of datetime.now()."""
        args: JsonObject = {}
        path = self._alloc_path()
        effect_id = Effect.make_id(self.run_id, path, "now", args)
        cached = self._lookup_effect(effect_id)
        if cached and cached.status == "ok":
            value = await self._resolve_effect_value(cached)
            return datetime.fromisoformat(value["ts"])
        ts = datetime.now(tz=timezone.utc)
        await self._record_effect(effect_id, "ok", {"ts": ts.isoformat()})
        return ts

    async def random(self) -> float:
        """Journaled random float — use this instead of random.random()."""
        args: JsonObject = {}
        path = self._alloc_path()
        effect_id = Effect.make_id(self.run_id, path, "random", args)
        cached = self._lookup_effect(effect_id)
        if cached and cached.status == "ok":
            value = await self._resolve_effect_value(cached)
            return float(value["value"])
        result = _random.random()
        await self._record_effect(effect_id, "ok", {"value": result})
        return result

    async def uuid(self) -> str:
        """Journaled UUID — use this instead of uuid.uuid4()."""
        args: JsonObject = {}
        path = self._alloc_path()
        effect_id = Effect.make_id(self.run_id, path, "uuid", args)
        cached = self._lookup_effect(effect_id)
        if cached and cached.status == "ok":
            value = await self._resolve_effect_value(cached)
            return str(value["value"])
        result = _uuid.uuid4().hex
        await self._record_effect(effect_id, "ok", {"value": result})
        return result


__all__ = ["_JournalMixin", "_ARTIFACT_OFFLOAD_BYTES"]
