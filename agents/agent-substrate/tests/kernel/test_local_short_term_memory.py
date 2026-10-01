"""Tests for LocalFilesystemShortTermMemory (kernel ShortTermMemory Protocol)."""

from __future__ import annotations

from pathlib import Path

from substrate.agents.storage import LocalFilesystemShortTermMemory


async def test_set_get_state_round_trip(tmp_path: Path) -> None:
    store = LocalFilesystemShortTermMemory(root=tmp_path)
    assert await store.get_state("s1") == {}

    await store.set_state("s1", {"a": 1, "b": "hi"})
    assert await store.get_state("s1") == {"a": 1, "b": "hi"}


async def test_update_state_merges_not_replaces(tmp_path: Path) -> None:
    store = LocalFilesystemShortTermMemory(root=tmp_path)
    await store.set_state("s1", {"a": 1})
    await store.update_state("s1", {"b": 2})
    assert await store.get_state("s1") == {"a": 1, "b": 2}

    await store.update_state("s1", {"a": 99})
    assert await store.get_state("s1") == {"a": 99, "b": 2}


async def test_clear_removes_state(tmp_path: Path) -> None:
    store = LocalFilesystemShortTermMemory(root=tmp_path)
    await store.set_state("s1", {"a": 1})
    await store.clear("s1")
    assert await store.get_state("s1") == {}
    # clearing a session that was never written is a no-op, not an error
    await store.clear("never-existed")


async def test_sessions_are_isolated(tmp_path: Path) -> None:
    store = LocalFilesystemShortTermMemory(root=tmp_path)
    await store.set_state("s1", {"a": 1})
    await store.set_state("s2", {"a": 2})
    assert await store.get_state("s1") == {"a": 1}
    assert await store.get_state("s2") == {"a": 2}


async def test_state_survives_restart(tmp_path: Path) -> None:
    store1 = LocalFilesystemShortTermMemory(root=tmp_path)
    await store1.set_state("s1", {"a": 1})

    store2 = LocalFilesystemShortTermMemory(root=tmp_path)
    assert await store2.get_state("s1") == {"a": 1}
