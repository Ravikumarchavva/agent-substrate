"""The ``Library`` tests run on both databases: a folder store (SQLite) and PostgreSQL (skipped when none is reachable)."""

from __future__ import annotations

import pytest

from substrate.stores import Store
from tests._postgres import schema_store


@pytest.fixture(params=["folder", pytest.param("postgres", marks=pytest.mark.requires_postgres)])
async def store(request, tmp_path):
    if request.param == "folder":
        store = Store.at(tmp_path / "store")
        await store.start()
        try:
            yield store
        finally:
            await store.aclose()
    else:
        async for postgres in schema_store(tmp_path):
            yield postgres
