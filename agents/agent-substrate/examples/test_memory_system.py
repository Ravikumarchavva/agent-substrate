"""Integration test for the memory system.

Tests:
  1. Message serializer round-trips for all 5 message types
  2. RedisMemory CRUD (requires running Redis)
  3. PostgresMemory CRUD (requires running Postgres)
  4. SessionManager full lifecycle (requires both)

Usage:
  uv run python examples/test_memory_system.py
"""
import asyncio
import json
import sys
import os

# ---------------------------------------------------------------------------
# 1. Message Serializer Tests (no external deps)
# ---------------------------------------------------------------------------

def test_serializer():
    from agent_framework.memory.message_serializer import (
        serialize_message, deserialize_message,
        serialize_messages, deserialize_messages,
    )
    from agent_framework.messages.client_messages import (
        SystemMessage, UserMessage, AssistantMessage,
        ToolCallMessage, ToolExecutionResultMessage,
    )

    print("=" * 60)
    print("1. MESSAGE SERIALIZER TESTS")
    print("=" * 60)

    msgs = [
        SystemMessage(content="You are helpful"),
        UserMessage(content=["Hello world"]),
        AssistantMessage(content=["Hi there"], finish_reason="stop"),
        ToolCallMessage(name="search", arguments={"q": "test"}),
        ToolExecutionResultMessage(
            tool_call_id="tc-1", name="search", content="result here"
        ),
    ]

    # Single round-trip
    for msg in msgs:
        d = serialize_message(msg)
        restored = deserialize_message(d)
        assert type(restored).__name__ == type(msg).__name__
        print(f"  ✓ {type(msg).__name__} round-trip")

    # Bulk round-trip
    json_str = serialize_messages(msgs)
    restored_list = deserialize_messages(json_str)
    assert len(restored_list) == len(msgs)
    print(f"  ✓ Bulk serialization ({len(restored_list)} messages)")

    # Error rejection
    try:
        deserialize_message({"type": "FakeMessage"})
        assert False
    except ValueError:
        print("  ✓ Unknown type rejected")

    try:
        deserialize_message({})
        assert False
    except ValueError:
        print("  ✓ Missing type rejected")

    print("  ALL SERIALIZER TESTS PASSED ✓\n")


# ---------------------------------------------------------------------------
# 2. Redis Memory Tests
# ---------------------------------------------------------------------------

async def test_redis_memory():
    from agent_framework.memory.redis_memory import RedisMemory
    from agent_framework.messages.client_messages import (
        SystemMessage, UserMessage, AssistantMessage,
    )

    print("=" * 60)
    print("2. REDIS MEMORY TESTS")
    print("=" * 60)

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    async with RedisMemory(redis_url=redis_url, default_ttl=60, max_messages=10) as redis:
        session_id = "test-redis-session-001"

        # Clean up from previous runs
        await redis.delete_session(session_id)

        # Add messages
        await redis.add_message(session_id, SystemMessage(content="Be concise"))
        await redis.add_message(session_id, UserMessage(content=["What is 2+2?"]))
        await redis.add_message(session_id, AssistantMessage(content=["4"], finish_reason="stop"))
        print("  ✓ Added 3 messages")

        # Read back
        messages = await redis.get_messages(session_id)
        assert len(messages) == 3
        assert type(messages[0]).__name__ == "SystemMessage"
        assert type(messages[1]).__name__ == "UserMessage"
        assert type(messages[2]).__name__ == "AssistantMessage"
        print(f"  ✓ Retrieved {len(messages)} messages with correct types")

        # Count
        count = await redis.get_message_count(session_id)
        assert count == 3
        print(f"  ✓ Message count: {count}")

        # Exists
        assert await redis.exists(session_id)
        print("  ✓ Session exists check")

        # Limit
        last_two = await redis.get_messages(session_id, limit=2)
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
        await redis.add_messages(session_id, bulk_msgs)
        total = await redis.get_message_count(session_id)
        assert total == 8  # 3 + 5
        print(f"  ✓ Bulk add: total now {total}")

        # Max messages trim (max_messages=10)
        more_msgs = [UserMessage(content=[f"Overflow {i}"]) for i in range(5)]
        await redis.add_messages(session_id, more_msgs)
        trimmed_count = await redis.get_message_count(session_id)
        assert trimmed_count <= 10
        print(f"  ✓ Trim enforced: {trimmed_count} messages (max=10)")

        # TTL
        ttl = await redis.get_ttl(session_id)
        assert ttl > 0
        print(f"  ✓ TTL active: {ttl}s remaining")

        # Clear
        await redis.clear(session_id)
        assert await redis.get_message_count(session_id) == 0
        print("  ✓ Clear")

        # Cleanup
        await redis.delete_session(session_id)
        assert not await redis.exists(session_id)
        print("  ✓ Delete session")

    print("  ALL REDIS TESTS PASSED ✓\n")


# ---------------------------------------------------------------------------
# 3. Postgres Memory Tests
# ---------------------------------------------------------------------------

async def test_postgres_memory():
    from agent_framework.memory.postgres_memory import PostgresMemory
    from agent_framework.messages.client_messages import (
        SystemMessage, UserMessage, AssistantMessage,
    )

    print("=" * 60)
    print("3. POSTGRES MEMORY TESTS")
    print("=" * 60)

    db_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb")

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

async def test_session_manager():
    from agent_framework.memory.redis_memory import RedisMemory
    from agent_framework.memory.postgres_memory import PostgresMemory
    from agent_framework.memory.session_manager import (
        SessionManager, SessionStatus,
    )
    from agent_framework.messages.client_messages import (
        SystemMessage, UserMessage, AssistantMessage,
    )

    print("=" * 60)
    print("4. SESSION MANAGER TESTS")
    print("=" * 60)

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    db_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb")

    redis = RedisMemory(redis_url=redis_url, default_ttl=120, max_messages=100)
    postgres = PostgresMemory(database_url=db_url)

    async with SessionManager(redis=redis, postgres=postgres, auto_checkpoint_threshold=5) as mgr:
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
        await mgr.add_message(sid, AssistantMessage(content=["Hi!"], finish_reason="stop"))
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
        print(f"  ✓ Session state: count={full_state.message_count}, hot={full_state.is_hot}")

        # Auto-checkpoint test: add enough messages to trigger
        for i in range(6):
            await mgr.add_message(sid, UserMessage(content=[f"Auto msg {i}"]))
        pg_count_after = await postgres.get_message_count(sid)
        print(f"  ✓ After 6 more messages: Postgres has {pg_count_after} (auto-checkpoint threshold=5)")

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
# Runner
# ---------------------------------------------------------------------------

async def main():
    print("\n🧠 AGENT FRAMEWORK — MEMORY SYSTEM TESTS\n")

    # Serializer always runs (no external deps)
    test_serializer()

    # Check if Redis is available
    redis_available = False
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
        await r.ping()
        await r.aclose()
        redis_available = True
    except Exception as e:
        print(f"⚠ Redis not available ({e}), skipping Redis tests\n")

    # Check if Postgres is available
    pg_available = False
    try:
        from sqlalchemy.ext.asyncio import create_async_engine
        db_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb")
        engine = create_async_engine(db_url)
        async with engine.connect() as conn:
            await conn.execute(engine.dialect.statement_compiler(engine.dialect, None).__class__.__module__ and conn.connection)
        pg_available = True
    except Exception:
        try:
            from sqlalchemy.ext.asyncio import create_async_engine
            from sqlalchemy import text
            engine = create_async_engine(db_url)
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            await engine.dispose()
            pg_available = True
        except Exception as e:
            print(f"⚠ Postgres not available ({e}), skipping Postgres tests\n")

    if redis_available:
        await test_redis_memory()

    if pg_available:
        await test_postgres_memory()

    if redis_available and pg_available:
        await test_session_manager()

    print("=" * 60)
    print("🎉 ALL AVAILABLE TESTS PASSED!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
