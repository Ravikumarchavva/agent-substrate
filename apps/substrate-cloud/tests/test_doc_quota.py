"""doc_quota — fixed-window daily Redis counters for document upload/commit
limits. Uses a minimal in-memory fake Redis (no real Redis needed) since the
logic under test is a handful of INCRBY/EXPIRE/GET calls, not Redis itself."""

from __future__ import annotations

from substrate.serving.shared.doc_quota import check_and_increment, peek, release


class _FakeRedis:
    """Just enough of the redis-py async API for doc_quota.py's calls."""

    def __init__(self) -> None:
        self.store: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    async def incrby(self, key: str, amount: int) -> int:
        self.store[key] = self.store.get(key, 0) + amount
        return self.store[key]

    async def decrby(self, key: str, amount: int) -> int:
        self.store[key] = self.store.get(key, 0) - amount
        return self.store[key]

    async def expire(self, key: str, seconds: int) -> None:
        self.ttls[key] = seconds

    async def get(self, key: str):
        val = self.store.get(key)
        return str(val) if val is not None else None

    async def set(self, key: str, value: int, *, keepttl: bool = False) -> None:
        self.store[key] = value


async def test_check_and_increment_allows_under_limit():
    redis = _FakeRedis()
    allowed, remaining = await check_and_increment(
        redis, "docquota:test", "user-1", limit=5
    )
    assert allowed is True
    assert remaining == 4


async def test_check_and_increment_sets_ttl_only_on_first_increment():
    redis = _FakeRedis()
    await check_and_increment(redis, "docquota:test", "user-1", limit=5)
    await check_and_increment(redis, "docquota:test", "user-1", limit=5)
    assert len(redis.ttls) == 1


async def test_check_and_increment_rejects_over_limit():
    redis = _FakeRedis()
    for _ in range(5):
        allowed, _ = await check_and_increment(
            redis, "docquota:test", "user-1", limit=5
        )
        assert allowed is True
    allowed, remaining = await check_and_increment(
        redis, "docquota:test", "user-1", limit=5
    )
    assert allowed is False
    assert remaining == 0


async def test_check_and_increment_multi_count():
    redis = _FakeRedis()
    allowed, remaining = await check_and_increment(
        redis, "docquota:test", "user-1", limit=5, count=3
    )
    assert allowed is True
    assert remaining == 2

    allowed2, remaining2 = await check_and_increment(
        redis, "docquota:test", "user-1", limit=5, count=3
    )
    assert allowed2 is False  # 3 + 3 = 6 > 5
    assert remaining2 == 0


async def test_different_users_have_independent_counters():
    redis = _FakeRedis()
    for _ in range(5):
        await check_and_increment(redis, "docquota:test", "user-1", limit=5)
    allowed, _ = await check_and_increment(redis, "docquota:test", "user-2", limit=5)
    assert allowed is True


async def test_release_gives_back_quota():
    redis = _FakeRedis()
    await check_and_increment(redis, "docquota:test", "user-1", limit=5, count=3)
    await release(redis, "docquota:test", "user-1", count=3)
    used, remaining = await peek(redis, "docquota:test", "user-1", limit=5)
    assert used == 0
    assert remaining == 5


async def test_release_never_goes_negative():
    redis = _FakeRedis()
    await release(redis, "docquota:test", "user-1", count=3)
    used, _ = await peek(redis, "docquota:test", "user-1", limit=5)
    assert used == 0


async def test_peek_does_not_increment():
    redis = _FakeRedis()
    await check_and_increment(redis, "docquota:test", "user-1", limit=5)
    used, remaining = await peek(redis, "docquota:test", "user-1", limit=5)
    assert used == 1
    assert remaining == 4
    used2, _ = await peek(redis, "docquota:test", "user-1", limit=5)
    assert used2 == 1  # unchanged — peek is read-only


async def test_peek_fresh_user_reads_zero():
    redis = _FakeRedis()
    used, remaining = await peek(redis, "docquota:test", "never-seen", limit=5)
    assert used == 0
    assert remaining == 5
