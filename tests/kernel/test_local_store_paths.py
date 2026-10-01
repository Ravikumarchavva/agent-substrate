"""Ids reach the local stores from request bodies and other untrusted places, so
none of them may ever address a path outside the store's own root."""

from __future__ import annotations

from pathlib import Path

import pytest

from substrate.agents.storage.fs import safe_name
from substrate.agents.storage.local_graph import LocalFilesystemGraphStore
from substrate.agents.storage.local_history import LocalFilesystemHistoryProvider
from substrate.agents.storage.local_memory_store import LocalFilesystemMemoryStore
from substrate.agents.storage.local_short_term_memory import LocalFilesystemShortTermMemory
from substrate.agents.storage.local_vector import LocalFilesystemVectorStore
from substrate.agents.workspace.local_workspace_store import LocalFilesystemWorkspaceStore
from substrate.kernel.core.content import ChatMessage, Role, TextBlock
from substrate.kernel.storage.history import MessageNode

HOSTILE = ["../../evil", "..", ".", "a/b", "a\\b", "/etc/passwd", "x/../../y", "..%2F..", "\x00"]


def _inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


@pytest.mark.parametrize("identifier", HOSTILE)
def test_safe_name_is_a_single_harmless_path_component(identifier: str):
    name = safe_name(identifier)
    assert "/" not in name and "\\" not in name and "\x00" not in name
    assert name not in ("", ".", "..")


def test_safe_name_is_injective_and_leaves_ordinary_ids_alone():
    assert safe_name("a/b") != safe_name("c/b")  # basename() would collapse these
    for ordinary in ["3f2b9c1e-aaaa-4bbb-8ccc-0123456789ab", "main", "exp-1", "run_7.v2", "abc123"]:
        assert safe_name(ordinary) == ordinary  # existing data keeps its file names


def test_empty_identifiers_are_rejected():
    with pytest.raises(ValueError):
        safe_name("")


@pytest.mark.parametrize("identifier", HOSTILE)
def test_every_local_store_keeps_paths_inside_its_root(tmp_path: Path, identifier: str):
    root = tmp_path / "store"
    paths = [
        LocalFilesystemShortTermMemory(root)._path(identifier),
        LocalFilesystemHistoryProvider(root)._node_path(identifier),
        LocalFilesystemHistoryProvider(root)._branch_path(identifier, identifier),
        LocalFilesystemHistoryProvider(root)._checkpoint_path(identifier, identifier),
        LocalFilesystemHistoryProvider(root)._session_dir(identifier),
        LocalFilesystemVectorStore(root)._doc_path(identifier, identifier),
        LocalFilesystemGraphStore(root)._entity_path(identifier),
        LocalFilesystemGraphStore(root)._relationship_path(identifier),
        LocalFilesystemMemoryStore(root)._record_path(identifier, identifier),
        LocalFilesystemWorkspaceStore(root)._snapshot_path(identifier),
        LocalFilesystemWorkspaceStore(root)._head_path(identifier, identifier),
    ]
    for path in paths:
        assert _inside(path, root), path


async def test_short_term_memory_sessions_never_share_state(tmp_path: Path):
    store = LocalFilesystemShortTermMemory(tmp_path)
    await store.set_state("a/b", {"who": "first"})
    await store.set_state("c/b", {"who": "second"})

    assert await store.get_state("a/b") == {"who": "first"}
    assert await store.get_state("c/b") == {"who": "second"}
    assert await store.get_state("../../evil") == {}
    assert not (tmp_path.parent / "evil.json").exists()


@pytest.mark.parametrize("hostile", ["..", "../victim", "../../victim", "../../../../tmp"])
async def test_deleting_a_hostile_session_id_removes_nothing_it_should_not(
    tmp_path: Path, hostile: str
):
    """``delete_session`` ends in ``shutil.rmtree`` — with an unsanitized id,
    ``..`` deletes the whole store and ``../../victim`` deletes a sibling."""
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "precious.txt").write_text("keep me")
    root = tmp_path / "store"
    history = LocalFilesystemHistoryProvider(root)
    await history.connect()
    (root / "sessions").mkdir()
    (root / "marker.txt").write_text("the store's own data")

    await history.delete_session(hostile)

    assert (victim / "precious.txt").read_text() == "keep me"
    assert (root / "marker.txt").read_text() == "the store's own data"


async def test_history_still_round_trips_with_a_hostile_branch_and_session(tmp_path: Path):
    history = LocalFilesystemHistoryProvider(tmp_path / "store")
    await history.connect()
    node = MessageNode(
        session_id="../s",
        run_id="r",
        payload=ChatMessage(role=Role.USER, content=[TextBlock(text="hi")]),
    )

    await history.append_and_advance(node, branch_id="../../b")

    branch = await history.get_branch("../s", "../../b")
    assert branch is not None and branch.head_message_id == node.id
    assert not list(tmp_path.glob("*.json")) and not (tmp_path.parent / "b.json").exists()


async def test_memory_store_finds_records_across_tenants_with_unusual_ids(tmp_path: Path):
    from substrate.kernel.storage.memory import (
        MemoryNamespace,
        MemoryRecord,
    )

    store = LocalFilesystemMemoryStore(tmp_path)
    ns = MemoryNamespace(tenant_id="acme/../corp", user_id="u")
    record = MemoryRecord(namespace=ns, content=[TextBlock(text="likes tea")])
    await store.save(record)

    assert (await store.get(record.id)) is not None
    assert all(_inside(p, tmp_path) for p in tmp_path.rglob("*"))
