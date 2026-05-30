"""Integration tests for the memory system.

Tests:
  1. Message serializer round-trips for all 5 message types
  2. RedisHistoryProvider CRUD (requires running Redis)
  3. PostgresHistoryProvider CRUD (requires running Postgres)
  4. TieredHistoryProvider full lifecycle (requires both)

Run via pytest:
  uv run pytest tests/extensions/memory/test_memory_system.py

Run standalone:
  uv run python tests/extensions/memory/test_memory_system.py
"""

from __future__ import annotations

import asyncio
import os

import pytest

from ravi.kernel.memory.message_serializer import (
    serialize_message,
    deserialize_message,
    serialize_messages,
    deserialize_messages,
)
from ravi.kernel.messages.client_messages import (
    SystemMessage,
    UserMessage,
    AssistantMessage,
    ToolCallMessage,
    ToolExecutionResultMessage,
)


def _redis_available() -> bool:
    """Check Redis connectivity synchronously for skip markers."""
    try:
        import redis as _redis

        r = _redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
        r.ping()
        r.close()
        return True
    except Exception:
        return False


def _pg_available() -> bool:
    """Check Postgres connectivity synchronously for skip markers."""
    import asyncio
    import socket

    db_url = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb",
    )
    # Extract host and port from URL for a lightweight TCP probe
    try:
        # e.g. postgresql+asyncpg://user:pass@host:5432/db
        netloc = db_url.split("@")[-1].split("/")[0]
        host, _, port_str = netloc.partition(":")
        port = int(port_str) if port_str else 5432
        sock = socket.create_connection((host, port), timeout=2)
        sock.close()
    except Exception:
        return False

    # Now verify with asyncpg that we can actually authenticate
    try:
        import asyncpg

        async def _check() -> bool:
            conn = await asyncpg.connect(
                db_url.replace("postgresql+asyncpg://", "postgresql://"), timeout=5
            )
            await conn.execute("SELECT 1")
            await conn.close()
            return True

        return asyncio.run(_check())
    except Exception:
        return False


requires_redis = pytest.mark.skipif(
    not _redis_available(), reason="Redis not available"
)
requires_postgres = pytest.mark.skipif(
    not _pg_available(), reason="Postgres not available"
)

# ---------------------------------------------------------------------------
# 1. Message Serializer Tests (no external deps)
# ---------------------------------------------------------------------------


_SAMPLE_MESSAGES = [
    SystemMessage(content="You are helpful"),
    UserMessage(content=["Hello world"]),
    AssistantMessage(content=["Hi there"], finish_reason="stop"),
    ToolCallMessage(name="search", arguments={"q": "test"}),
    ToolExecutionResultMessage(
        tool_call_id="tc-1", name="search", content="result here"
    ),
]


@pytest.mark.parametrize("msg", _SAMPLE_MESSAGES, ids=lambda m: type(m).__name__)
def test_serializer_single_roundtrip(msg):
    d = serialize_message(msg)
    restored = deserialize_message(d)
    assert type(restored).__name__ == type(msg).__name__


def test_serializer_bulk_roundtrip():
    json_str = serialize_messages(_SAMPLE_MESSAGES)
    restored = deserialize_messages(json_str)
    assert len(restored) == len(_SAMPLE_MESSAGES)


def test_serializer_rejects_unknown_type():
    with pytest.raises(ValueError):
        deserialize_message({"type": "FakeMessage"})


def test_serializer_rejects_missing_type():
    with pytest.raises(ValueError):
        deserialize_message({})


# ---------------------------------------------------------------------------
# 2. Redis Memory Tests
# ---------------------------------------------------------------------------


@requires_redis
async def test_redis_history():
    from ravi.adapters.memory.redis_history import RedisHistoryProvider

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    async with RedisHistoryProvider(
        redis_url=redis_url, ttl=60, max_messages=10
    ) as redis:
        session_id = "test-redis-session-001"

        # Clean up from previous runs
        await redis.clear_session(session_id)

        # Add messages
        await redis.save_messages(
            session_id,
            [
                SystemMessage(content="Be concise"),
                UserMessage(content=["What is 2+2?"]),
                AssistantMessage(content=["4"], finish_reason="stop"),
            ],
        )

        # Read back
        messages = await redis.load_messages(session_id)
        assert len(messages) == 3
        assert type(messages[0]).__name__ == "SystemMessage"
        assert type(messages[1]).__name__ == "UserMessage"
        assert type(messages[2]).__name__ == "AssistantMessage"
        print(f"  ✓ Retrieved {len(messages)} messages with correct types")

        # Count
        assert await redis.count_messages(session_id) == 3
        print("  ✓ Message count: 3")

        # Limit
        last_two = await redis.load_messages(session_id, limit=2)
        assert len(last_two) == 2
        print(f"  ✓ Limited retrieval: {len(last_two)} messages")

        # Bulk add
        bulk_msgs = [UserMessage(content=[f"Message {i}"]) for i in range(5)]
        await redis.save_messages(session_id, bulk_msgs)
        total = await redis.count_messages(session_id)
        assert total == 8  # 3 + 5
        print(f"  ✓ Bulk add: total now {total}")

        # Max messages trim (max_messages=10)
        more_msgs = [UserMessage(content=[f"Overflow {i}"]) for i in range(5)]
        await redis.save_messages(session_id, more_msgs)
        trimmed_count = await redis.count_messages(session_id)
        assert trimmed_count <= 10
        print(f"  ✓ Trim enforced: {trimmed_count} messages (max=10)")

        # TTL refresh (no error)
        await redis.refresh_ttl(session_id)
        print("  ✓ TTL refreshed")

        # Clear
        await redis.clear_session(session_id)
        assert await redis.count_messages(session_id) == 0
        print("  ✓ Clear session")

    print("  ALL REDIS TESTS PASSED ✓\n")


# ---------------------------------------------------------------------------
# 3. Postgres Memory Tests
# ---------------------------------------------------------------------------


@requires_postgres
async def test_postgres_history():
    from ravi.adapters.memory.postgres_history import PostgresHistoryProvider

    print("=" * 60)
    print("3. POSTGRES HISTORY TESTS")
    print("=" * 60)

    db_url = os.getenv(
        "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb"
    )

    async with PostgresHistoryProvider(database_url=db_url) as pg:
        session_id = "test-pg-session-001"

        # Clean up
        await pg.clear_session(session_id)

        # Save messages (session row auto-created)
        msgs = [
            SystemMessage(content="Be helpful"),
            UserMessage(content=["Hi"]),
            AssistantMessage(content=["Hello!"], finish_reason="stop"),
        ]
        saved = await pg.save_messages(session_id, msgs)
        assert saved == 3
        print(f"  ✓ Saved {saved} messages")

        # Load messages
        loaded = await pg.load_messages(session_id)
        assert len(loaded) == 3
        assert type(loaded[0]).__name__ == "SystemMessage"
        print(f"  ✓ Loaded {len(loaded)} messages with correct types")

        # Count
        assert await pg.count_messages(session_id) == 3
        print("  ✓ Message count: 3")

        # Append more (sequence continues)
        await pg.save_messages(session_id, [UserMessage(content=["again"])])
        assert await pg.count_messages(session_id) == 4
        print("  ✓ Append continues sequence: 4")

        # Partial load (last N, ascending)
        partial = await pg.load_messages(session_id, limit=2)
        assert len(partial) == 2
        assert partial[-1].content == ["again"]
        print(f"  ✓ Partial load: {len(partial)} messages")

        # Clear messages
        await pg.clear_session(session_id)
        assert await pg.count_messages(session_id) == 0
        print("  ✓ Cleared session")

    print("  ALL POSTGRES TESTS PASSED ✓\n")


# ---------------------------------------------------------------------------
# 4. Session Manager Tests
# ---------------------------------------------------------------------------


@requires_redis
@requires_postgres
async def test_tiered_provider():
    from ravi.adapters.memory.redis_history import RedisHistoryProvider
    from ravi.adapters.memory.postgres_history import PostgresHistoryProvider
    from ravi.agents.memory.tiered import TieredHistoryProvider

    print("=" * 60)
    print("4. TIERED PROVIDER TESTS")
    print("=" * 60)

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    db_url = os.getenv(
        "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb"
    )

    cache = RedisHistoryProvider(redis_url=redis_url, ttl=120, max_messages=100)
    store = PostgresHistoryProvider(database_url=db_url)

    async with TieredHistoryProvider(
        cache=cache, store=store, checkpoint_every=5
    ) as tiered:
        sid = "test-tiered-session-001"
        await tiered.clear_session(sid)

        # Add messages (write-through to cache)
        await tiered.save_messages(sid, [SystemMessage(content="You are a test agent")])
        await tiered.save_messages(sid, [UserMessage(content=["Hello"])])
        await tiered.save_messages(
            sid, [AssistantMessage(content=["Hi!"], finish_reason="stop")]
        )
        print("  ✓ Added 3 messages")

        # Read messages (served from cache)
        messages = await tiered.load_messages(sid)
        assert len(messages) == 3
        print(f"  ✓ Retrieved {len(messages)} messages")

        # Manual checkpoint flushes cache → store
        saved = await tiered.checkpoint(sid)
        assert saved == 3
        assert await store.count_messages(sid) == 3
        print("  ✓ Checkpoint flushed 3 messages to store")

        # Auto-checkpoint: pushing past threshold=5 flushes automatically
        for i in range(6):
            await tiered.save_messages(sid, [UserMessage(content=[f"Auto msg {i}"])])
        assert await store.count_messages(sid) >= 5
        print(f"  ✓ Auto-checkpoint: store has {await store.count_messages(sid)}")

        # Cold read: clear the cache, load should fall through to the store
        await cache.clear_session(sid)
        cold = await tiered.load_messages(sid)
        assert len(cold) > 0
        print(f"  ✓ Cold read from store warmed cache: {len(cold)} messages")

        # Clear everything
        await tiered.clear_session(sid)
        assert await tiered.count_messages(sid) == 0
        print("  ✓ Cleared both tiers")

    print("  ALL TIERED PROVIDER TESTS PASSED ✓\n")


# ---------------------------------------------------------------------------
# Standalone runner (for `uv run python tests/test_memory_system.py`)
# ---------------------------------------------------------------------------


async def main():
    print("\nAGENT FRAMEWORK - MEMORY SYSTEM TESTS\n")

    # Serializer always runs (no external deps)
    for msg in _SAMPLE_MESSAGES:
        test_serializer_single_roundtrip(msg)
    test_serializer_bulk_roundtrip()
    test_serializer_rejects_unknown_type()
    test_serializer_rejects_missing_type()
    print("  Serializer tests passed\n")

    if _redis_available():
        await test_redis_history()
    else:
        print("  Redis not available, skipping Redis tests\n")

    if _pg_available():
        await test_postgres_history()
    else:
        print("  Postgres not available, skipping Postgres tests\n")

    if _redis_available() and _pg_available():
        await test_tiered_provider()

    print("ALL AVAILABLE TESTS PASSED!")


if __name__ == "__main__":
    asyncio.run(main())
