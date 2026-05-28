"""Example 2-1: Memory Backends — raw memory operations across all three storage tiers."""

import os

from ravi.kernel.memory.unbounded_memory import UnboundedMemory
from ravi.integrations.memory.redis_memory import RedisMemory
from ravi.integrations.memory.postgres_memory import PostgresMemory
from ravi.extensions.context.redis_model_context import RedisModelContext
from ravi.kernel.messages.client_messages import (
    SystemMessage,
    UserMessage,
    AssistantMessage,
)

# Infrastructure:
#   Section 2 (RedisMemory):    Redis required     — make infra-up
#   Section 3 (PostgresMemory): PostgreSQL required — make infra-up
#   Section 4 (Stateless):      Redis required     — make infra-up

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb",
)


async def main() -> None:
    # ---
    # Section 1: UnboundedMemory — in-process, no infrastructure required
    # ---
    print("=== 1. UnboundedMemory (no infra) ===")

    mem = UnboundedMemory()

    await mem.add_message(SystemMessage(content="You are a helpful assistant."))
    await mem.add_message(UserMessage(content=["What is the capital of France?"]))
    await mem.add_message(AssistantMessage(content=["Paris."], finish_reason="stop"))
    await mem.add_message(UserMessage(content=["And Germany?"]))
    await mem.add_message(AssistantMessage(content=["Berlin."], finish_reason="stop"))

    all_msgs = await mem.get_messages()
    print(f"  Total messages stored : {len(all_msgs)}")

    window = await mem.get_messages(limit=4)
    print(f"  Sliding window (last 4): {[type(m).__name__ for m in window]}")

    tokens = await mem.get_token_count()
    print(f"  Approx token count    : {tokens}")

    await mem.clear()
    print(f"  After clear           : {len(await mem.get_messages())} messages")

    # ---
    # Section 2: RedisMemory — requires Redis (make infra-up)
    # ---
    print("\n=== 2. RedisMemory (requires Redis) ===")
    try:
        SESSION_A = "demo-backend-session-a"
        redis_a = RedisMemory(
            session_id=SESSION_A, redis_url=REDIS_URL, default_ttl=3600
        )
        await redis_a.connect()

        # restore() reloads any prior history; returns 0 on a fresh session
        prior = await redis_a.restore()
        print(f"  Prior messages restored: {prior}")

        await redis_a.add_message(UserMessage(content=["Hello from Redis!"]))
        await redis_a.add_message(
            AssistantMessage(content=["Hello back!"], finish_reason="stop")
        )
        print(f"  Messages in session   : {len(await redis_a.get_messages())}")

        # Sliding window from local cache — no network round-trip
        recent = await redis_a.get_messages(limit=1)
        print(f"  Most recent type      : {type(recent[0]).__name__}")

        # Session isolation: for_session() clones a handle with a different key
        SESSION_B = "demo-backend-session-b"
        redis_b = RedisMemory.for_session(redis_a, SESSION_B)
        isolated = await redis_b.restore()
        print(f"  Session B (isolated)  : {isolated} messages")  # 0 — different key

        # Same session_id on a new handle picks up the stored state
        redis_a2 = RedisMemory.for_session(redis_a, SESSION_A)
        picked_up = await redis_a2.restore()
        print(f"  New handle on A       : {picked_up} messages restored from Redis")

        await redis_a.clear()
        await redis_a.disconnect()
    except Exception as exc:
        print(f"  [SKIP] Redis unavailable: {exc}")

    # ---
    # Section 3: PostgresMemory — requires PostgreSQL (make infra-up)
    # ---
    print("\n=== 3. PostgresMemory (requires PostgreSQL) ===")
    try:
        postgres = PostgresMemory(database_url=DB_URL)
        await postgres.connect()

        SESSION_PG = "demo-backend-pg"
        await postgres.create_session(
            session_id=SESSION_PG, agent_name="backend-demo"
        )

        saved = await postgres.save_messages(
            SESSION_PG,
            [
                UserMessage(content=["Postgres turn 1"]),
                AssistantMessage(
                    content=["Postgres reply 1"], finish_reason="stop"
                ),
            ],
        )
        print(f"  Messages saved        : {saved}")

        msgs = await postgres.load_messages(SESSION_PG)
        print(f"  Messages loaded       : {len(msgs)}")

        count = await postgres.get_message_count(SESSION_PG)
        print(f"  get_message_count()   : {count}")

        await postgres.clear_messages(SESSION_PG)
        after_clear = await postgres.load_messages(SESSION_PG)
        print(f"  After clear_messages(): {len(after_clear)} messages")

        await postgres.delete_session(SESSION_PG)
        print("  Session deleted cleanly")

        await postgres.disconnect()
    except Exception as exc:
        print(f"  [SKIP] Postgres unavailable: {exc}")

    # ---
    # Section 4: Stateless agent pattern — RedisMemory + RedisModelContext
    #
    # Key insight: the only state needed to recreate the conversation is the
    # session_id.  Two completely fresh Python objects can share the same
    # conversation because all history lives in Redis, not in the process.
    # ---
    print("\n=== 4. Stateless agent pattern ===")
    try:
        SESSION_SL = "demo-stateless"

        # Turn 1 — actor writes to Redis, then exits (disconnect)
        turn1 = RedisMemory(
            session_id=SESSION_SL, redis_url=REDIS_URL, default_ttl=3600
        )
        await turn1.connect()
        await turn1.restore()
        await turn1.add_message(UserMessage(content=["What's 2 + 2?"]))
        await turn1.add_message(
            AssistantMessage(content=["4."], finish_reason="stop")
        )
        print(
            f"  Turn 1: wrote {len(await turn1.get_messages())} messages — disconnecting"
        )
        await turn1.disconnect()

        # Turn 2 — entirely fresh objects, same session_id; state comes from Redis
        turn2 = RedisMemory(
            session_id=SESSION_SL, redis_url=REDIS_URL, default_ttl=3600
        )
        await turn2.connect()
        restored_count = await turn2.restore()
        print(f"  Turn 2: fresh object, restored {restored_count} messages from Redis")

        ctx = RedisModelContext(redis_memory=turn2, recent_n=10)
        context_msgs = await ctx.build(
            session_id=SESSION_SL,
            current_input="Continue the conversation",
            raw_messages=[],  # RedisModelContext ignores this — reads from Redis
        )
        print(
            f"  Context visible to LLM: {[type(m).__name__ for m in context_msgs]}"
        )

        await turn2.clear()
        await turn2.disconnect()
    except Exception as exc:
        print(f"  [SKIP] Redis unavailable: {exc}")

    # ---
    # Section 5: Quick reference
    # ---
    print("\n=== 5. Quick reference ===")
    print("  Imports:")
    print("    from ravi.kernel.memory.unbounded_memory import UnboundedMemory")
    print("    from ravi.integrations.memory.redis_memory import RedisMemory")
    print("    from ravi.integrations.memory.postgres_memory import PostgresMemory")
    print("    from ravi.extensions.context.redis_model_context import RedisModelContext")
    print("  Key methods:")
    print("    UnboundedMemory  : add_message(), get_messages(limit=N), clear(), get_token_count()")
    print("    RedisMemory      : connect(), restore(), add_message(), get_messages(limit=N), clear(), disconnect()")
    print("    RedisMemory      : for_session(parent, session_id)  ← share connection pool")
    print("    RedisModelContext: build(session_id, current_input, raw_messages=[])  ← ignores raw_messages")
    print("    PostgresMemory   : connect(), create_session(), save_messages(), load_messages(), disconnect()")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
