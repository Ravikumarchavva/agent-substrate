import pytest
from unittest.mock import AsyncMock, MagicMock
from ravi.extensions.memory.session_manager import SessionManager, SessionStatus
from ravi.integrations.memory.redis_memory import RedisMemory
from ravi.integrations.memory.postgres_memory import PostgresMemory
from ravi.kernel.memory._lineage import LineageStore, ProvenanceTag, LineageRecord, StorageTier

@pytest.fixture
def redis():
    return AsyncMock(spec=RedisMemory)

@pytest.fixture
def postgres():
    return AsyncMock(spec=PostgresMemory)

@pytest.fixture
def lineage_store():
    return AsyncMock(spec=LineageStore)

@pytest.fixture
def cold_store():
    store = AsyncMock(spec=LineageStore)
    store.tier = StorageTier.COLD
    return store

@pytest.mark.asyncio
async def test_archive_session_tier_routing(redis, postgres, lineage_store, cold_store):
    manager = SessionManager(
        redis=redis,
        postgres=postgres,
        lineage_store=lineage_store,
        cold_store=cold_store
    )
    
    # Mock some lineage records in WARM
    prov = ProvenanceTag(agent_fqn="test", activation_id="run1", timestamp_utc="2026-05-28T00:00:00Z")
    record = LineageRecord(session_id="sess1", message_id="msg1", provenance=prov, tier=StorageTier.WARM)
    lineage_store.list_session.return_value = [record]
    
    redis.fetch.return_value = []
    
    await manager.archive_session("sess1")
    
    # Verify records transferred to cold store
    cold_store.record.assert_called_once_with("sess1", "msg1", prov)
    
    # Verify dropped from warm store
    lineage_store.drop_session.assert_called_once_with("sess1")
    
    # Verify postgres status updated
    postgres.update_session_status.assert_called_once_with("sess1", SessionStatus.ARCHIVED.value)
    
    # Verify redis cleaned up
    redis.delete_session.assert_called_once_with("sess1")
