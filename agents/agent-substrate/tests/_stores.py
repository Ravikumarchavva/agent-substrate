"""Throw-away durable stores for tests: each is a fresh folder under one temp root, removed at exit.

There is no in-memory store in the kernel (a store that forgets on exit is not what an agent rests on),
so a test that needs a thread store, task board or file store gets the real one, on a store in an empty folder.
"""

from __future__ import annotations

import tempfile

from substrate.stores import Store
from substrate.stores.file_tables import Files
from substrate.stores.thread_tables import Threads
from substrate.stores.task_tables import Tasks

_ROOT = tempfile.TemporaryDirectory(prefix="substrate-tests-")


def folder() -> str:
    return tempfile.mkdtemp(dir=_ROOT.name)


def fs_history() -> Threads:
    return Store.at(folder()).threads


def fs_tasks() -> Tasks:
    return Store.at(folder()).tasks


def fs_files() -> Files:
    return Store.at(folder(), file_quota_bytes=10**9).files
