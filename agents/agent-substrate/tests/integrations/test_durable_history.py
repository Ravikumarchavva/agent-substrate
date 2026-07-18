from __future__ import annotations

import os
import pytest
from sqlalchemy.exc import OperationalError

from substrate.capabilities.history import DurableHistoryProvider
from substrate.kernel import AgentId, ChatMessage
from substrate.kernel.core.content import TextBlock

pytestmark = [pytest.mark.requires_postgres]


def test_postgres_history_internal_key_fits_legacy_column() -> None:
    provider = DurableHistoryProvider("postgresql+asyncpg://user:pass@localhost/db")
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
    provider = DurableHistoryProvider(db_url, echo=False)

    try:
        await provider.connect()
    except (OperationalError, Exception) as e:
        pytest.skip(f"PostgreSQL database not available: {e}")

    agent_id = AgentId(type="assistant", key="test-agent")
    session_id = "test-session-456"

    try:
        # Write clean state using protocol methods
        await provider.clear(agent_id, session_id=session_id)
        assert await provider.count_messages(agent_id, session_id=session_id) == 0

        # Save a message via the protocol
        msg = ChatMessage(role="user", content=[TextBlock(text="postgres message")])
        await provider.append(agent_id, msg, session_id=session_id, run_id="r1")

        # Load via the protocol
        loaded = await provider.get_messages(agent_id, session_id=session_id)
        assert len(loaded) == 1
        assert loaded[0].role == "user"
        assert loaded[0].content[0].text == "postgres message"  # type: ignore[union-attr]

        # Count via the protocol
        assert await provider.count_messages(agent_id, session_id=session_id) == 1

        # Cleanup via the protocol
        await provider.clear(agent_id, session_id=session_id)
        assert await provider.count_messages(agent_id, session_id=session_id) == 0
    finally:
        await provider.disconnect()
