"""Integration tests for the memory system.

Tests:
  1. Message serializer round-trips for all 5 message types
  2. RedisMemory CRUD (requires running Redis)
  3. PostgresMemory CRUD (requires running Postgres)
  4. SessionManager full lifecycle (requires both)

Run via pytest:
  uv run pytest tests/test_memory_system.py

Run standalone:
  uv run python tests/test_memory_system.py
"""

from __future__ import annotations

import asyncio
import os

import pytest

from raavan.core.memory.message_serializer import (
    serialize_message,
    deserialize_message,
    serialize_messages,
    deserialize_messages,
)
from raavan.core.messages.client_messages import (
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
    try:
        from sqlalchemy import create_engine, text

        db_url = os.getenv(
            "DATABASE_URL",
            "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb",
        )
        sync_url = db_url.replace("+asyncpg", "")
        engine = create_engine(sync_url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
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
async def test_redis_memory():
    from raavan.core.memory.redis_memory import RedisMemory

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    async with RedisMemory(
        redis_url=redis_url, default_ttl=60, max_messages=10
    ) as redis:
        session_id = "test-redis-session-001"

        # Clean up from previous runs
        await redis.delete_session(session_id)

        # Add messages
        await redis.store(session_id, SystemMessage(content="Be concise"))
        await redis.store(session_id, UserMessage(content=["What is 2+2?"]))
        await redis.store(
            session_id, AssistantMessage(content=["4"], finish_reason="stop")
        )

        # Read back
        messages = await redis.fetch(session_id)
        assert len(messages) == 3
        assert type(messages[0]).__name__ == "SystemMessage"
        assert type(messages[1]).__name__ == "UserMessage"
        assert type(messages[2]).__name__ == "AssistantMessage"
        print(f"  ✓ Retrieved {len(messages)} messages with correct types")

        # Count
        count = await redis.count(session_id)
        assert count == 3
        print(f"  ✓ Message count: {count}")

        # Exists
        assert await redis.exists(session_id)
        print("  ✓ Session exists check")

        # Limit
        last_two = await redis.fetch(session_id, limit=2)
        assert len(last_two) == 2
        print(f"  ✓ Limited retrieval: {len(last_two)} messages")

        # Metadata
        await redis.set_metadata(session_id, {"agent": "test", "turn": 1})
        meta = await redis.get_metadata(session_id)
        assert meta["agent"] == "test"
        assert meta["turn"] == 1
        print(f"  ✓ Metadata round-trip: {meta}")

        # Bulk add
        bulk_msgs = [UserMessage(content=[f"Message {i}"]) for i in range(5)]
        await redis.store_many(session_id, bulk_msgs)
        total = await redis.count(session_id)
        assert total == 8  # 3 + 5
        print(f"  ✓ Bulk add: total now {total}")

        # Max messages trim (max_messages=10)
        more_msgs = [UserMessage(content=[f"Overflow {i}"]) for i in range(5)]
        await redis.store_many(session_id, more_msgs)
        trimmed_count = await redis.count(session_id)
        assert trimmed_count <= 10
        print(f"  ✓ Trim enforced: {trimmed_count} messages (max=10)")

        # TTL
        ttl = await redis.get_ttl(session_id)
        assert ttl > 0
        print(f"  ✓ TTL active: {ttl}s remaining")

        # Clear
        await redis.drop(session_id)
        assert await redis.count(session_id) == 0
        print("  ✓ Clear")

        # Cleanup
        await redis.delete_session(session_id)
        assert not await redis.exists(session_id)
        print("  ✓ Delete session")

    print("  ALL REDIS TESTS PASSED ✓\n")


# ---------------------------------------------------------------------------
# 3. Postgres Memory Tests
# ---------------------------------------------------------------------------


@requires_postgres
async def test_postgres_memory():
    from raavan.core.memory.postgres_memory import PostgresMemory
    from raavan.core.messages.client_messages import (
        SystemMessage,
        UserMessage,
        AssistantMessage,
    )

    print("=" * 60)
    print("3. POSTGRES MEMORY TESTS")
    print("=" * 60)

    db_url = os.getenv(
        "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb"
    )

    async with PostgresMemory(database_url=db_url) as pg:
        session_id = "test-pg-session-001"

        # Clean up
        await pg.delete_session(session_id)

        # Create session
        session = await pg.create_session(
            session_id=session_id,
            agent_name="test-agent",
            user_id="user-42",
            metadata={"env": "test"},
        )
        assert session.id == session_id
        print(f"  ✓ Created session: {session}")

        # Save messages
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
        count = await pg.get_message_count(session_id)
        assert count == 3
        print(f"  ✓ Message count: {count}")

        # Session retrieval
        pg_session = await pg.get_session(session_id)
        assert pg_session is not None
        assert pg_session.agent_name == "test-agent"
        print(f"  ✓ Retrieved session: agent={pg_session.agent_name}")

        # List sessions
        sessions = await pg.list_sessions(agent_name="test-agent")
        assert len(sessions) >= 1
        print(f"  ✓ Listed sessions: {len(sessions)} found")

        # Partial load
        partial = await pg.load_messages(session_id, limit=2)
        assert len(partial) == 2
        print(f"  ✓ Partial load: {len(partial)} messages")

        # Clear messages
        await pg.clear_messages(session_id)
        assert await pg.get_message_count(session_id) == 0
        print("  ✓ Cleared messages")

        # Delete session
        await pg.delete_session(session_id)
        assert await pg.get_session(session_id) is None
        print("  ✓ Deleted session")

    print("  ALL POSTGRES TESTS PASSED ✓\n")


# ---------------------------------------------------------------------------
# 4. Session Manager Tests
# ---------------------------------------------------------------------------


@requires_redis
@requires_postgres
async def test_session_manager():
    from raavan.core.memory.redis_memory import RedisMemory
    from raavan.core.memory.postgres_memory import PostgresMemory
    from raavan.core.memory.session_manager import (
        SessionManager,
        SessionStatus,
    )
    from raavan.core.messages.client_messages import (
        SystemMessage,
        UserMessage,
        AssistantMessage,
    )

    print("=" * 60)
    print("4. SESSION MANAGER TESTS")
    print("=" * 60)

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    db_url = os.getenv(
        "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb"
    )

    redis = RedisMemory(redis_url=redis_url, default_ttl=120, max_messages=100)
    postgres = PostgresMemory(database_url=db_url)

    async with SessionManager(
        redis=redis, postgres=postgres, auto_checkpoint_threshold=5
    ) as mgr:
        # Create session
        state = await mgr.create_session(
            agent_name="react-agent",
            user_id="user-99",
            metadata={"purpose": "test"},
        )
        sid = state.session_id
        assert state.status == SessionStatus.ACTIVE
        assert state.is_hot
        print(f"  ✓ Created session: {sid[:16]}...")

        # Add messages
        await mgr.add_message(sid, SystemMessage(content="You are a test agent"))
        await mgr.add_message(sid, UserMessage(content=["Hello"]))
        await mgr.add_message(
            sid, AssistantMessage(content=["Hi!"], finish_reason="stop")
        )
        print("  ✓ Added 3 messages")

        # Read messages
        messages = await mgr.get_messages(sid)
        assert len(messages) == 3
        print(f"  ✓ Retrieved {len(messages)} messages")

        # Checkpoint
        saved = await mgr.checkpoint(sid)
        assert saved == 3
        print(f"  ✓ Checkpointed {saved} messages to Postgres")

        # Verify Postgres has the data
        pg_count = await postgres.get_message_count(sid)
        assert pg_count == 3
        print(f"  ✓ Postgres confirms {pg_count} messages")

        # Get session state
        full_state = await mgr.get_session_state(sid)
        assert full_state is not None
        assert full_state.message_count == 3
        assert full_state.is_hot
        print(
            f"  ✓ Session state: count={full_state.message_count}, hot={full_state.is_hot}"
        )

        # Auto-checkpoint test: add enough messages to trigger
        for i in range(6):
            await mgr.add_message(sid, UserMessage(content=[f"Auto msg {i}"]))
        pg_count_after = await postgres.get_message_count(sid)
        print(
            f"  ✓ After 6 more messages: Postgres has {pg_count_after} (auto-checkpoint threshold=5)"
        )

        # Close session
        await mgr.close_session(sid)
        print("  ✓ Session closed")

        # Verify Redis cleaned up
        assert not await redis.exists(sid)
        print("  ✓ Redis cleaned up after close")

        # Verify Postgres still has data
        pg_session = await postgres.get_session(sid)
        assert pg_session is not None
        assert pg_session.status == "closed"
        print(f"  ✓ Postgres session status: {pg_session.status}")

        # Resume from cold storage
        resumed = await mgr.resume_session(sid)
        assert resumed.is_hot
        assert resumed.message_count > 0
        print(f"  ✓ Resumed session: {resumed.message_count} messages reloaded")

        # Verify messages are accessible again
        restored_msgs = await mgr.get_messages(sid)
        assert len(restored_msgs) > 0
        print(f"  ✓ Messages accessible: {len(restored_msgs)}")

        # List sessions
        all_sessions = await mgr.list_sessions(agent_name="react-agent")
        assert len(all_sessions) >= 1
        print(f"  ✓ Listed sessions: {len(all_sessions)} found")

        # Delete permanently
        await mgr.delete_session(sid)
        assert await mgr.get_session_state(sid) is None
        print("  ✓ Session permanently deleted")

    print("  ALL SESSION MANAGER TESTS PASSED ✓\n")


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
        await test_redis_memory()
    else:
        print("  Redis not available, skipping Redis tests\n")

    if _pg_available():
        await test_postgres_memory()
    else:
        print("  Postgres not available, skipping Postgres tests\n")

    if _redis_available() and _pg_available():
        await test_session_manager()

    print("ALL AVAILABLE TESTS PASSED!")


if __name__ == "__main__":
    asyncio.run(main())
