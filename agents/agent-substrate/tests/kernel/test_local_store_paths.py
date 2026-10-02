"""Ids reach the local stores from request bodies and other untrusted places, so
none of them may ever address a path outside the store's own root."""

from __future__ import annotations

from pathlib import Path

import pytest

from substrate.stores.local.fs import safe_name
from substrate.stores import LocalFilesystemGraphStore
from substrate.stores import LocalFilesystemVectorStore
from substrate.workspace import LocalFilesystemWorkspaceStore

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
        LocalFilesystemVectorStore(root)._doc_path(identifier, identifier),
        LocalFilesystemGraphStore(root)._entity_path(identifier),
        LocalFilesystemGraphStore(root)._relationship_path(identifier),
        LocalFilesystemWorkspaceStore(root)._snapshot_path(identifier),
        LocalFilesystemWorkspaceStore(root)._head_path(identifier, identifier),
    ]
    for path in paths:
        assert _inside(path, root), path
