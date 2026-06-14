"""InMemoryJournal — Stage 0 in-process implementation of Journal."""

from __future__ import annotations

from ravi.kernel.runtime.effects import EffectResult


class InMemoryJournal:
    """Single-process in-memory Journal.

    Write-once: recording an effect_id a second time is a silent no-op so
    the at-most-once guarantee holds even if a replay races a live path.
    """

    def __init__(self) -> None:
        self._cache: dict[str, EffectResult] = {}

    async def lookup(self, effect_id: str) -> EffectResult | None:
        return self._cache.get(effect_id)

    async def record(self, result: EffectResult) -> None:
        self._cache.setdefault(result.effect_id, result)
