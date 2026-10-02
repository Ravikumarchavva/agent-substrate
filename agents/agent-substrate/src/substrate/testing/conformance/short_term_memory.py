"""Conformance suite for ``ShortTermMemory``.

The key/value state one conversation keeps across runs. Every implementation — the store's own, a Redis cache, any a
consumer writes — runs exactly these tests: state round-trips, a patch merges instead of replacing, concurrent patches
never lose each other's keys, and one session's state never shows up in another's. Subclass it and provide the ``store``
fixture.
"""

from __future__ import annotations

import asyncio

import pytest

from substrate.stores.memory import ShortTermMemory

_HOSTILE = [
    "x' OR '1'='1",
    "a'; DROP TABLE t; --",
    "../../etc/passwd",
    "a/b\\c",
    "..",
    "ünï-çødé",
    "x" * 200,
]


class ShortTermMemoryConformance:
    @pytest.fixture
    async def store(
        self,
    ) -> ShortTermMemory:  # pragma: no cover - supplied by subclasses
        raise NotImplementedError

    @pytest.fixture(autouse=True)
    async def _clean_slate(self, store: ShortTermMemory) -> None:
        """A store that outlives a test (Redis) must not carry one test's sessions into the next."""
        for session in ["s1", "s2", "burst", *_HOSTILE]:
            await store.clear(session)

    async def test_an_unknown_session_has_empty_state(
        self, store: ShortTermMemory
    ) -> None:
        assert await store.get_state("s1") == {}

    async def test_state_round_trips(self, store: ShortTermMemory) -> None:
        state = {
            "a": 1,
            "b": "hi",
            "nested": {"xs": [1, 2, 3], "ok": True},
            "none": None,
        }
        await store.set_state("s1", state)
        assert await store.get_state("s1") == state

    async def test_set_replaces_the_whole_state(self, store: ShortTermMemory) -> None:
        await store.set_state("s1", {"a": 1, "b": 2})
        await store.set_state("s1", {"c": 3})
        assert await store.get_state("s1") == {"c": 3}

    async def test_update_merges_and_leaves_other_keys_alone(
        self, store: ShortTermMemory
    ) -> None:
        await store.set_state("s1", {"a": 1})
        await store.update_state("s1", {"b": 2})
        await store.update_state("s1", {"a": 99})
        assert await store.get_state("s1") == {"a": 99, "b": 2}

    async def test_updating_an_unknown_session_creates_it(
        self, store: ShortTermMemory
    ) -> None:
        await store.update_state("s1", {"a": 1})
        assert await store.get_state("s1") == {"a": 1}

    async def test_concurrent_updates_never_lose_a_key(
        self, store: ShortTermMemory
    ) -> None:
        """Agents patch state concurrently; each patch is atomic, so after all of them every key is there."""
        await asyncio.gather(
            *(store.update_state("burst", {f"k{i}": i}) for i in range(25))
        )
        assert await store.get_state("burst") == {f"k{i}": i for i in range(25)}

    async def test_clear_removes_state_and_clearing_nothing_is_fine(
        self, store: ShortTermMemory
    ) -> None:
        await store.set_state("s1", {"a": 1})
        await store.clear("s1")
        assert await store.get_state("s1") == {}
        await store.clear("s1")

    async def test_sessions_never_share_state(self, store: ShortTermMemory) -> None:
        await store.set_state("s1", {"who": "first"})
        await store.set_state("s2", {"who": "second"})
        assert await store.get_state("s1") == {"who": "first"}
        assert await store.get_state("s2") == {"who": "second"}
        await store.clear("s1")
        assert await store.get_state("s2") == {"who": "second"}

    @pytest.mark.parametrize("hostile", _HOSTILE)
    async def test_a_hostile_session_id_is_inert(
        self, store: ShortTermMemory, hostile: str
    ) -> None:
        await store.set_state("s1", {"who": "bystander"})
        await store.set_state(hostile, {"who": "hostile"})
        assert await store.get_state(hostile) == {"who": "hostile"}
        assert await store.get_state("s1") == {"who": "bystander"}
        await store.clear(hostile)
        assert await store.get_state(hostile) == {}
        assert await store.get_state("s1") == {"who": "bystander"}


__all__ = ["ShortTermMemoryConformance"]
