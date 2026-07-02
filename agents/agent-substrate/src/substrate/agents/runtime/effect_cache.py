"""EffectCache — per-run effect-result cache, built by folding the EventLog.

This is the "fold" half of the kernel's fold-is-truth doctrine
(``kernel/runtime/log_entry.py``): reconstructing a run's effect-dedup state
from its append-only log instead of a separate, independently-TTL'd store.

It replaces the Journal as ``RunContext``'s source of truth for at-most-once
effect execution. Effect results are ``effect.result`` EventLog entries — the
same durable, optimistically-concurrent, cross-process-safe store that
already backs run history — so there is no longer a Redis journal whose TTL
can silently expire mid-run and break the at-most-once guarantee (a run
suspended or orphaned longer than the TTL used to come back to a journal
miss on every effect: LLM calls re-billed, tools re-executed).

Built once per lease (``Worker._run_agent``), read many times during that
run — lookups are a plain dict access, no I/O.
"""

from __future__ import annotations

from substrate.kernel.runtime.effects import EffectResult
from substrate.kernel.runtime.ids import RunId
from substrate.kernel.runtime.log_entry import EventLog


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
    async def fold(cls, event_log: EventLog, run_id: RunId) -> "EffectCache":
        """Reconstruct the effect cache by reading a run's full log.

        ``last_seq`` seeds ``RunContext``'s local seq cursor, so the Worker
        never needs a separate ``last_seq()`` query before its first append.
        """
        effects: dict[str, EffectResult] = {}
        last_seq = -1
        async for entry in event_log.read(run_id):
            last_seq = entry.seq
            if entry.kind == "effect.result":
                p = entry.payload
                effects[p["effect_id"]] = EffectResult(
                    effect_id=p["effect_id"],
                    status=p["status"],
                    value=p.get("value") or {},
                    artifact_ref=p.get("artifact_ref"),
                )
        return cls(run_id=run_id, effects=effects, last_seq=last_seq)


__all__ = ["EffectCache"]
