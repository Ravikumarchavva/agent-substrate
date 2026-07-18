"""EffectCache — per-run effect-result cache, built by folding the EventLogProtocol.

This is the "fold" half of the kernel's fold-is-truth doctrine
(``kernel/runtime/log_entry.py``): reconstructing a run's effect-dedup state
from its append-only log instead of a separate, independently-TTL'd store.

It replaces the Journal as ``RunContext``'s source of truth for at-most-once
effect execution. Effect results are ``effect.result`` EventLogProtocol entries — the
same durable, optimistically-concurrent, cross-process-safe store that
already backs run history — so there is no longer a Redis journal whose TTL
can silently expire mid-run and break the at-most-once guarantee (a run
suspended or orphaned longer than the TTL used to come back to a journal
miss on every effect: LLM calls re-billed, tools re-executed).

Built once per lease (``Worker._run_agent``), read many times during that
run — lookups are a plain dict access, no I/O.

Error effects are deliberately excluded from the fold (see ``fold()``): a
scheduler retry must re-execute a failed effect, not replay its failure
forever from cache. They're still written to the EventLogProtocol by
``RunContext._record_effect`` for the durable per-attempt record — this only
affects what a *fresh* fold (a new lease, i.e. every retry) rehydrates.
"""

from __future__ import annotations

from substrate.kernel.runtime.effects import EffectResult
from substrate.kernel.runtime.ids import RunId
from substrate.kernel.runtime.log_entry import EventLogProtocol


class EffectCache:
    """In-memory ``effect_id -> EffectResult`` lookup for one run."""

    def __init__(
        self,
        run_id: RunId,
        effects: dict[str, EffectResult],
        last_seq: int,
    ) -> None:
        self.run_id = run_id
        self.last_seq = last_seq
        self._effects = effects

    def lookup(self, effect_id: str) -> EffectResult | None:
        return self._effects.get(effect_id)

    def put(self, result: EffectResult) -> None:
        self._effects[result.effect_id] = result

    @classmethod
    async def fold(cls, event_log: EventLogProtocol, run_id: RunId) -> "EffectCache":
        """Reconstruct the effect cache by reading a run's full log.

        ``last_seq`` seeds ``RunContext``'s local seq cursor, so the Worker
        never needs a separate ``last_seq()`` query before its first append.

        ``status == "error"`` entries are skipped: a failed effect must be a
        cache MISS on the next lease so the scheduler's retry genuinely
        re-executes it, rather than re-raising the same cached failure on
        every attempt (see module docstring). A later successful attempt at
        the same effect_id overwrites the dict entry as usual, since it's a
        forward scan over the log in seq order.
        """
        effects: dict[str, EffectResult] = {}
        last_seq = -1
        async for entry in event_log.read(run_id):
            last_seq = entry.seq
            if entry.kind == "effect.result":
                p = entry.payload
                if p["status"] == "error":
                    effects.pop(p["effect_id"], None)
                    continue
                effects[p["effect_id"]] = EffectResult(
                    effect_id=p["effect_id"],
                    status=p["status"],
                    value=p.get("value") or {},
                    artifact_ref=p.get("artifact_ref"),
                )
        return cls(run_id=run_id, effects=effects, last_seq=last_seq)


__all__ = ["EffectCache"]
