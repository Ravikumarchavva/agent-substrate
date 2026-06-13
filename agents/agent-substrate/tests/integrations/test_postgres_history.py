from __future__ import annotations

import os
import pytest
from sqlalchemy.exc import OperationalError
from ravi.capabilities.history import PostgresHistoryProvider
from ravi.kernel import AgentId, ChatMessage
from ravi.kernel.core.content import TextBlock


def test_postgres_history_internal_key_fits_legacy_column() -> None:
    provider = PostgresHistoryProvider("postgresql+asyncpg://user:pass@localhost/db")
    agent_id = AgentId(type="assistant", key="agent-" + ("x" * 80))
    session_id = "session-" + ("y" * 120)

    storage_key = provider._session_key(agent_id, session_id)

    assert len(storage_key) <= 128
    assert storage_key.startswith("h:")


@pytest.mark.asyncio
async def test_postgres_history_provider():
    # Fallback to local dev postgres db url if environment is not set
    db_url = os.getenv(
        "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb"
    )
    provider = PostgresHistoryProvider(db_url, echo=False)

    try:
        await provider.connect()
    except (OperationalError, Exception) as e:
        pytest.skip(f"PostgreSQL database not available: {e}")

    try:
        session_id = "test-session-456"

        # Write clean state
        await provider.clear_session(session_id)
        assert await provider.count_messages(session_id) == 0

        # Save messages
        msg = ChatMessage(role="user", content=[TextBlock(text="postgres message")])
        saved_count = await provider.save_messages(session_id, [msg])
        assert saved_count == 1

        # Load messages
        loaded = await provider.load_messages(session_id)
        assert len(loaded) == 1
        assert loaded[0].role == "user"
        assert loaded[0].content[0].text == "postgres message"

        # Count
        assert await provider.count_messages(session_id) == 1

        # Cleanup
        await provider.clear_session(session_id)
        assert await provider.count_messages(session_id) == 0
    finally:
        await provider.disconnect()
