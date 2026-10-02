"""Throw-away durable stores for tests: each is a fresh folder under one temp root, removed at exit.

There is no in-memory store in the kernel (a store that forgets on exit is not what an agent rests on),
so a test that needs a thread store, task board or file store gets the real filesystem one in an empty folder.
"""

from __future__ import annotations

import tempfile

from substrate.stores import Store
from substrate.stores.thread_tables import Threads
from substrate.stores import WorkspaceFileStore
from substrate.stores import LocalFilesystemTaskStore

_ROOT = tempfile.TemporaryDirectory(prefix="substrate-tests-")


def folder() -> str:
    return tempfile.mkdtemp(dir=_ROOT.name)


def fs_history() -> Threads:
    return Store.at(folder()).threads


def fs_tasks() -> LocalFilesystemTaskStore:
    return LocalFilesystemTaskStore(folder())


def fs_files() -> WorkspaceFileStore:
    return WorkspaceFileStore(folder(), user_quota_bytes=10**9)
