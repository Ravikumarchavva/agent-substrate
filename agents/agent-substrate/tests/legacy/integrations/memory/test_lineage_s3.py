import pytest
import json
from unittest.mock import AsyncMock, MagicMock
from ravi.adapters.memory.lineage_s3 import S3LineageStore
from ravi.kernel.memory._lineage import ProvenanceTag, LineageNotFoundError

@pytest.fixture
def mock_s3_client():
    client = AsyncMock()
    return client

@pytest.fixture
def lineage_store(mock_s3_client):
    return S3LineageStore(mock_s3_client, "test-bucket", "lineage")

@pytest.mark.asyncio
async def test_record_and_get(lineage_store, mock_s3_client):
    prov = ProvenanceTag(
        agent_fqn="test_agent",
        activation_id="test_run",
        timestamp_utc="2026-05-28T00:00:00Z"
    )
    
    await lineage_store.record("sess1", "msg1", prov)
    mock_s3_client.put_object.assert_called_once()
    
    # Mock get
    mock_body = AsyncMock()
    data = {
        "session_id": "sess1",
        "message_id": "msg1",
        "tier": "COLD",
        "provenance": {
            "agent_fqn": "test_agent",
            "activation_id": "test_run",
            "timestamp_utc": "2026-05-28T00:00:00Z"
        }
    }
    mock_body.read.return_value = json.dumps(data).encode("utf-8")
    mock_s3_client.get_object.return_value = {"Body": mock_body}
    
    record = await lineage_store.get("sess1", "msg1")
    assert record.session_id == "sess1"
    assert record.message_id == "msg1"
    assert record.provenance.agent_fqn == "test_agent"
    
@pytest.mark.asyncio
async def test_get_not_found(lineage_store, mock_s3_client):
    class NoSuchKeyError(Exception):
        pass
    NoSuchKeyError.__name__ = "NoSuchKey"
    
    mock_s3_client.get_object.side_effect = NoSuchKeyError("key not found")
    
    with pytest.raises(LineageNotFoundError):
        await lineage_store.get("sess1", "msg2")
