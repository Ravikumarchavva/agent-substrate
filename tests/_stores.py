"""Throw-away durable stores for tests: each is a fresh folder under one temp root, removed at exit.

There is no in-memory store in the kernel (a store that forgets on exit is not what an agent rests on),
so a test that needs a history, task board or file store gets the real filesystem one in an empty folder.
"""

from __future__ import annotations

import tempfile

from substrate.kernel.storage.local_history import LocalFilesystemHistoryProvider
from substrate.kernel.storage.local_object_store import WorkspaceFileStore
from substrate.kernel.storage.local_tasks import LocalFilesystemTaskStore

_ROOT = tempfile.TemporaryDirectory(prefix="substrate-tests-")


def folder() -> str:
    return tempfile.mkdtemp(dir=_ROOT.name)


def fs_history() -> LocalFilesystemHistoryProvider:
    return LocalFilesystemHistoryProvider(folder())


def fs_tasks() -> LocalFilesystemTaskStore:
    return LocalFilesystemTaskStore(folder())


def fs_files() -> WorkspaceFileStore:
    return WorkspaceFileStore(folder(), user_quota_bytes=10**9)
