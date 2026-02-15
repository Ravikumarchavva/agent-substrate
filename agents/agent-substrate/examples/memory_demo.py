"""Demo: Memory system with persistent data.

This demo creates a session with messages and persists them to Postgres
without deleting at the end, so you can inspect the database.

Run with:
  uv run python examples/memory_demo.py
"""
import asyncio
import os
from datetime import datetime

from agent_framework.memory.redis_memory import RedisMemory
from agent_framework.memory.postgres_memory import PostgresMemory
from agent_framework.memory.session_manager import SessionManager
from agent_framework.messages.client_messages import (
    SystemMessage, UserMessage, AssistantMessage, ToolCallMessage, ToolExecutionResultMessage,
)


async def main():
    print("\n🧠 MEMORY SYSTEM DEMO\n")

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    db_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb")

    # Initialize storage backends
    redis = RedisMemory(redis_url=redis_url, default_ttl=3600, max_messages=500)
    postgres = PostgresMemory(database_url=db_url)

    async with SessionManager(redis=redis, postgres=postgres) as mgr:
        # Create a new session
        print("Creating session...")
        session = await mgr.create_session(
            agent_name="demo-agent",
            user_id="user-123",
            metadata={"purpose": "demo", "created_at": datetime.now().isoformat()},
        )
        sid = session.session_id
        print(f"  ✓ Session ID: {sid}\n")

        # Add some messages
        print("Adding messages to session...")
        messages = [
            SystemMessage(content="You are a helpful assistant."),
            UserMessage(content=["What is the capital of France?"]),
            AssistantMessage(
                content=["The capital of France is Paris."],
                finish_reason="stop",
            ),
            ToolCallMessage(name="search", arguments={"query": "Paris population"}),
            ToolExecutionResultMessage(
                tool_call_id="tc-1",
                name="search",
                content="Paris population is about 2.2 million.",
            ),
            UserMessage(content=["Thanks, that's helpful."]),
            AssistantMessage(
                content=["You're welcome! Feel free to ask any other questions."],
                finish_reason="stop",
            ),
        ]

        await mgr.add_messages(sid, messages)
        print(f"  ✓ Added {len(messages)} messages\n")

        # Checkpoint to Postgres
        print("Checkpointing to Postgres...")
        saved = await mgr.checkpoint(sid)
        print(f"  ✓ Saved {saved} messages\n")

        # Show session state
        print("Session state:")
        state = await mgr.get_session_state(sid)
        print(f"  ID:           {state.session_id}")
        print(f"  Agent:        {state.agent_name}")
        print(f"  User:         {state.user_id}")
        print(f"  Status:       {state.status.value}")
        print(f"  Messages:     {state.message_count}")
        print(f"  In Redis:     {state.is_hot}")
        print(f"  Metadata:     {state.metadata}\n")

        # Display messages
        print("Messages in session:")
        stored_messages = await mgr.get_messages(sid)
        for i, msg in enumerate(stored_messages, 1):
            msg_type = type(msg).__name__
            if hasattr(msg, 'content'):
                content_preview = str(msg.content)[:60]
            else:
                content_preview = "N/A"
            print(f"  {i}. [{msg_type}] {content_preview}")
        print()

        # List all sessions in the database
        print("Sessions in database:")
        all_sessions = await mgr.list_sessions(limit=10)
        for s in all_sessions:
            print(f"  - {s.session_id} | agent={s.agent_name} | msgs={s.message_count} | {s.status.value}")
        print()

        # Instructions for manual inspection
        print("=" * 70)
        print("🔍 DATABASE INSPECTION")
        print("=" * 70)
        print(f"\nSession ID: {sid}\n")
        print("Connect to database and run:")
        print("  psql -U postgres -d agentdb\n")
        print("View sessions:")
        print("  SELECT id, agent_name, status, message_count FROM memory_sessions;")
        print(f"  \\x  -- expand to see all columns\n")
        print("View messages for this session:")
        print(f"  SELECT sequence, message_type, payload FROM memory_messages")
        print(f"    WHERE session_id = '{sid}'")
        print(f"    ORDER BY sequence;")
        print("\nView message payloads (formatted):")
        print(f"  SELECT sequence, message_type,")
        print(f"         jsonb_pretty(payload) as payload_pretty")
        print(f"    FROM memory_messages")
        print(f"    WHERE session_id = '{sid}'")
        print(f"    ORDER BY sequence;")
        print()


if __name__ == "__main__":
    asyncio.run(main())
