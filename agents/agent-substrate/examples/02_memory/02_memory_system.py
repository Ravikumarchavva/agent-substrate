"""Example 2-2: Memory System — end-to-end SessionManager integration for multi-turn chat."""

import os

from ravi.integrations.memory.redis_memory import RedisMemory
from ravi.integrations.memory.postgres_memory import PostgresMemory
from ravi.extensions.memory.session_manager import SessionManager
from ravi.kernel.messages.client_messages import (
    SystemMessage,
    UserMessage,
    AssistantMessage,
    ToolCallMessage,
    ToolExecutionResultMessage,
)
from ravi.kernel.messages.content import TextBlock

# Infrastructure: Redis + PostgreSQL required (make infra-up)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb",
)


async def main() -> None:
    try:
        # ---
        # Section 1: SessionManager setup
        #
        # Redis   = hot tier  (fast, TTL-based, local cache per session)
        # Postgres = cold tier (durable, queryable, source-of-truth after checkpoint)
        # ---
        print("=== 1. SessionManager setup ===")

        redis = RedisMemory(redis_url=REDIS_URL, default_ttl=3600, max_messages=500)
        postgres = PostgresMemory(database_url=DB_URL)

        async with SessionManager(redis=redis, postgres=postgres) as mgr:
            # ---
            # Section 2: Create session
            # ---
            print("\n=== 2. Create session ===")
            session = await mgr.create_session(
                agent_name="chat-agent",
                user_id="user-demo",
                metadata={"channel": "web", "locale": "en-US"},
            )
            sid = session.session_id
            print(f"  session_id : {sid}")
            print(f"  agent_name : {session.agent_name}")
            print(f"  status     : {session.status.value}")
            print(f"  is_hot     : {session.is_hot}")

            # ---
            # Section 3: Add all 5 message types
            # ---
            print("\n=== 3. Add all 5 message types ===")

            tool_call = ToolCallMessage(
                name="web_search",
                arguments={"query": "current Python version"},
            )
            tool_result = ToolExecutionResultMessage(
                tool_call_id=tool_call.id,
                name="web_search",
                content=[TextBlock(text="Python 3.13 was released in October 2024.")],
            )

            await mgr.add_messages(
                sid,
                [
                    SystemMessage(content="You are a helpful Python assistant."),
                    UserMessage(content=["What is the latest Python version?"]),
                    tool_call,
                    tool_result,
                    AssistantMessage(
                        content=["Python 3.13 is the latest stable release."],
                        finish_reason="stop",
                    ),
                ],
            )

            count = await mgr.get_message_count(sid)
            print(f"  Messages added: {count}")

            msgs = await mgr.get_messages(sid)
            for m in msgs:
                print(f"    {type(m).__name__}")

            # ---
            # Section 4: Checkpoint to Postgres
            #
            # Flushes the current Redis state to Postgres so messages survive
            # Redis TTL expiry.  Hot path (Redis) → Cold path (Postgres).
            # ---
            print("\n=== 4. Checkpoint to Postgres ===")
            saved = await mgr.checkpoint(sid)
            print(f"  Messages persisted: {saved}")
            print("  Redis (hot) → Postgres (cold) flush complete")

            state = await mgr.get_session_state(sid)
            print(f"  message_count : {state.message_count}")
            print(f"  is_hot        : {state.is_hot}")

            # ---
            # Section 5: Restore session (resume from cold storage)
            #
            # A brand-new SessionManager calls resume_session() to reload
            # a session from Postgres back into Redis.  Simulates a new
            # deployment or an expired Redis key.
            # ---
            print("\n=== 5. Restore session ===")

        # Fresh SessionManager — simulates a new process / cold start
        redis2 = RedisMemory(redis_url=REDIS_URL, default_ttl=3600, max_messages=500)
        postgres2 = PostgresMemory(database_url=DB_URL)

        async with SessionManager(redis=redis2, postgres=postgres2) as mgr2:
            resumed = await mgr2.resume_session(sid)
            print(f"  Resumed session  : {resumed.session_id}")
            print(f"  message_count    : {resumed.message_count}")
            print(f"  is_hot           : {resumed.is_hot}")

            restored_msgs = await mgr2.get_messages(sid)
            print(f"  Messages visible : {len(restored_msgs)}")
            for m in restored_msgs:
                print(f"    {type(m).__name__}")

            # ---
            # Section 6: List sessions
            # ---
            print("\n=== 6. List sessions ===")
            sessions = await mgr2.list_sessions(agent_name="chat-agent", limit=10)
            print(f"  Found {len(sessions)} session(s) for agent 'chat-agent':")
            for s in sessions:
                print(
                    f"    {s.session_id[:8]}…  "
                    f"status={s.status.value}  "
                    f"msgs={s.message_count}  "
                    f"hot={s.is_hot}"
                )

            # ---
            # Section 7: Session lifecycle — close then delete
            # ---
            print("\n=== 7. Session lifecycle ===")

            await mgr2.close_session(sid)
            print("  close_session() — final checkpoint + status=closed + Redis cleaned")

            closed_state = await mgr2.get_session_state(sid)
            print(f"  Status after close : {closed_state.status.value}")
            print(f"  is_hot after close : {closed_state.is_hot}")

            await mgr2.delete_session(sid)
            gone = await mgr2.get_session_state(sid)
            print(f"  After delete_session(): get_session_state() → {gone}")

    except Exception as exc:
        print(
            f"\n[SKIP] Redis or Postgres unavailable — start services with `make infra-up`\n"
            f"       Error: {exc}"
        )


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
