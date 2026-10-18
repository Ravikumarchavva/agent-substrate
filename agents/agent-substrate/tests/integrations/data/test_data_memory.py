"""``DataMemoryStore`` against a real substrate-data (in this process): the ``MemoryStore`` conformance suite, the cache and fail-soft reads."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from substrate.integrations.data import DataMemoryStore, DataServiceConnection
from substrate.stores.memory import MemoryNamespace, MemoryQuery, MemoryRecord
from substrate.testing.conformance.memory_store import MemoryStoreConformance
from substrate.types.errors import AuthError, ScopeViolationError

from .conftest import DocsService, dead_url

pytestmark = pytest.mark.integration


class TestDataMemoryStore(MemoryStoreConformance):
    """The very suite every other MemoryStore runs, unchanged."""

    @pytest.fixture
    async def store(self, docs_service: DocsService) -> AsyncIterator[DataMemoryStore]:
        connection = DataServiceConnection(docs_service.url)
        store = DataMemoryStore(connection, docs_service.tokens(), cache_seconds=0)
        await self.clean(store)
        yield store
        await self.clean(store)
        await connection.aclose()

    @staticmethod
    async def clean(store: DataMemoryStore) -> None:
        """The service outlives a test (a store per test would restart it): empty the tenants the suite uses."""
        for tenant in ("acme", "evilcorp", "acme' OR '1'='1"):
            await store.erase(MemoryNamespace(tenant_id=tenant))


class Clock:
    now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
async def connection(docs_service: DocsService) -> AsyncIterator[DataServiceConnection]:
    connection = DataServiceConnection(docs_service.url)
    yield connection
    await connection.aclose()


def alice(tenant: str = "t1") -> MemoryNamespace:
    return MemoryNamespace(tenant_id=tenant, user_id="alice")


async def test_repeated_reads_within_the_ttl_hit_the_cache_and_a_write_drops_it(
    docs_service: DocsService, connection: DataServiceConnection
):
    clock, calls = Clock(), []
    base = docs_service.tokens()

    def counting(tenant: str, user: str | None) -> str:
        calls.append(1)
        return base(tenant, user)

    store = DataMemoryStore(connection, counting, clock=clock)
    record = MemoryRecord.from_text("alice likes cached tea", namespace=alice())
    await store.save(record)
    query = MemoryQuery(namespace=alice(), text_query="cached")
    first = [m.id for m in await store.query(query)]
    before = len(calls)
    assert [m.id for m in await store.query(query)] == first and len(
        calls
    ) == before  # no call
    clock.now += 61
    await store.query(query)
    assert len(calls) == before + 1  # expired
    await store.get(alice(), record.id)
    await store.save(MemoryRecord.from_text("another", namespace=alice(), id=record.id))
    assert (
        await store.get(alice(), record.id)
    ).text == "another"  # a write invalidated what was cached


async def test_reads_fail_soft_when_the_service_is_down_but_writes_do_not(
    docs_service: DocsService, connection: DataServiceConnection
):
    clock = Clock()
    store = DataMemoryStore(connection, docs_service.tokens(), clock=clock)
    record = MemoryRecord.from_text("remember the stale fact", namespace=alice())
    await store.save(record)
    query = MemoryQuery(namespace=alice(), text_query="stale")
    assert [m.id for m in await store.query(query)] == [record.id]
    await store.get(alice(), record.id)

    down = DataServiceConnection(dead_url(), timeout=1)
    try:
        offline = DataMemoryStore(down, docs_service.tokens(), clock=clock)
        assert await offline.query(query) == []  # nothing cached: empty, not an error
        assert await offline.get(alice(), record.id) is None
        await offline.touch(alice(), [record.id])  # swallowed
        with pytest.raises(Exception) as refused:
            await offline.save(record)  # a write is never pretended
        assert "unavailable" in type(
            refused.value
        ).__name__.lower() or "Unavailable" in str(type(refused.value))
        # a store that had the answer keeps serving it, even past the ttl
        offline._cache.update(store._cache)  # noqa: SLF001 - the same process would have cached it while up
        clock.now += 600
        assert [m.id for m in await offline.query(query)] == [record.id]
    finally:
        await down.aclose()


async def test_a_token_without_the_scope_is_an_auth_error(
    docs_service: DocsService, connection
):
    weak = DataMemoryStore(
        connection, docs_service.tokens(("documents:write",)), cache_seconds=0
    )
    with pytest.raises(AuthError):
        await weak.save(MemoryRecord.from_text("x", namespace=alice()))


async def test_taking_over_another_users_id_is_a_scope_violation(
    docs_service: DocsService, connection
):
    store = DataMemoryStore(connection, docs_service.tokens(), cache_seconds=0)
    mine = MemoryRecord.from_text("mine", namespace=alice())
    await store.save(mine)
    with pytest.raises(ScopeViolationError):
        await store.save(
            MemoryRecord.from_text(
                "theirs",
                id=mine.id,
                namespace=MemoryNamespace(tenant_id="t1", user_id="bob"),
            )
        )


async def test_a_refused_token_is_soft_for_reads_and_an_auth_error_for_writes(
    connection,
):
    def refuse(tenant: str, user: str | None) -> str:
        raise ValueError("the user is not a member of the organization")

    store = DataMemoryStore(connection, refuse)
    assert await store.get(alice(), "x") is None
    assert await store.query(MemoryQuery(namespace=alice())) == []
    with pytest.raises(AuthError):
        await store.save(MemoryRecord.from_text("x", namespace=alice()))
