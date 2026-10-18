"""Tests for loamz.agents.data adapters: tokens, files, memory, library."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import jwt

from loamz.agents.data import (
    DataFileStore,
    DataLibrary,
    DataMemoryStore,
    DataServiceConnection,
    DataTokenMinter,
)
from loamz.data.storage.memory import MemoryNamespace, MemoryQuery, MemoryRecord


def test_data_token_minter_roundtrip(tmp_path):
    key_file = tmp_path / "data-token.pem"
    minter = DataTokenMinter(key_file, issuer="test-issuer")

    token = minter.mint(
        "acme",
        "u1",
        agent_id="scout",
        run_id="run-1",
        thread_id="thread-1",
        scopes={"files:read", "memory:write"},
    )
    assert token

    jwks = minter.jwks()
    assert len(jwks["keys"]) == 1
    key_info = jwks["keys"][0]
    assert key_info["kty"] == "OKP"
    assert key_info["crv"] == "Ed25519"
    assert key_info["kid"] == minter.kid

    # Verify token payload
    decoded = jwt.decode(
        token,
        minter.key.public_key(),
        algorithms=["EdDSA"],
        issuer="test-issuer",
    )
    assert decoded["sub"] == "u1"
    assert decoded["tenant_id"] == "acme"
    assert "memory:write" in decoded["scope"].split()
    assert decoded["act"]["sub"] == "agent:scout"
    assert decoded["run_id"] == "run-1"
    assert decoded["thread_id"] == "thread-1"


async def test_data_file_store():
    conn = MagicMock(spec=DataServiceConnection)
    client = MagicMock()
    conn.client = client
    conn.bearer.return_value.__enter__ = MagicMock()
    conn.bearer.return_value.__exit__ = MagicMock()

    client.files = MagicMock()
    client.files.upload = AsyncMock()
    client.files.read = AsyncMock(return_value=b"hello bytes")
    client.files.exists = AsyncMock(return_value=True)
    client.files.delete = AsyncMock()
    client.files.delete_prefix = AsyncMock(return_value={"deleted": 3})
    client.files.copy_prefix = AsyncMock(return_value={"copied": 3})

    store = DataFileStore(conn, lambda t, u: "test-token")

    # upload & download
    await store.upload("tenants/acme/users/u1/hello.txt", b"hello bytes")
    client.files.upload.assert_awaited_once()

    data = await store.download("tenants/acme/users/u1/hello.txt")
    assert data == b"hello bytes"

    assert await store.exists("tenants/acme/users/u1/hello.txt") is True

    await store.delete("tenants/acme/users/u1/hello.txt")
    client.files.delete.assert_awaited_once()

    deleted = await store.delete_prefix("tenants/acme/users/u1/")
    assert deleted == 3

    copied = await store.copy_prefix("tenants/acme/users/u1/", "tenants/acme/users/u2/")
    assert copied == 3


async def test_data_memory_store():
    conn = MagicMock(spec=DataServiceConnection)
    client = MagicMock()
    conn.client = client
    conn.bearer.return_value.__enter__ = MagicMock()
    conn.bearer.return_value.__exit__ = MagicMock()

    rec = MemoryRecord.from_text("user likes tea", namespace=MemoryNamespace(tenant_id="acme", user_id="u1"))
    client.memory = MagicMock()
    client.memory.save = AsyncMock(return_value="rec-1")
    client.memory.get = AsyncMock(return_value=rec.model_dump(mode="json"))
    client.memory.delete = AsyncMock()
    client.memory.query = AsyncMock(return_value=[{"record": rec.model_dump(mode="json"), "score": 0.95, "rank": 1}])
    client.memory.touch = AsyncMock()
    client.memory.erase = AsyncMock(return_value=1)

    store = DataMemoryStore(conn, lambda t, u: "test-token")

    saved_id = await store.save(rec)
    assert saved_id == rec.id
    client.memory.save.assert_awaited_once()

    retrieved = await store.get(MemoryNamespace(tenant_id="acme", user_id="u1"), rec.id)
    assert retrieved is not None
    assert retrieved.text == "user likes tea"

    deleted = await store.delete(MemoryNamespace(tenant_id="acme", user_id="u1"), rec.id)
    assert deleted is True

    matches = await store.query(MemoryQuery(namespace=MemoryNamespace(tenant_id="acme", user_id="u1"), text_query="tea"))
    assert len(matches) == 1
    assert matches[0].record.text == "user likes tea"

    await store.touch(MemoryNamespace(tenant_id="acme", user_id="u1"), [rec.id])
    client.memory.touch.assert_awaited_once()

    erased = await store.erase(MemoryNamespace(tenant_id="acme", user_id="u1"))
    assert erased == 1


async def test_data_library():
    conn = MagicMock(spec=DataServiceConnection)
    client = MagicMock()
    conn.client = client
    conn.bearer.return_value.__enter__ = MagicMock()
    conn.bearer.return_value.__exit__ = MagicMock()

    client.documents = MagicMock()
    client.documents.ensure = AsyncMock()
    client.documents.add = AsyncMock(
        return_value={
            "document": "doc-1",
            "title": "Handbook",
            "filename": "handbook.txt",
            "sections": 2,
            "pages": 1,
            "images": 0,
            "sha256": "abc",
        }
    )
    client.documents.get = AsyncMock(
        return_value={
            "document": "doc-1",
            "title": "Handbook",
            "filename": "handbook.txt",
            "engine": "native",
            "resource": None,
            "pages": 1,
            "sections": 2,
            "images": 0,
            "needs_ocr": (),
            "topics": (),
            "sha256": "abc",
            "enrichment": "done",
            "created_at": 1000.0,
        }
    )
    client.documents.delete = AsyncMock()
    client.documents.search = AsyncMock(return_value={"hits": []})

    lib = DataLibrary(conn, lambda t, u: "test-token")

    added = await lib.add(
        b"some content", "handbook.txt", collection="tenants/acme/knowledge/handbook"
    )
    assert added.document == "doc-1"

    info = await lib.info("tenants/acme/knowledge/handbook", "doc-1")
    assert info is not None
    assert info.title == "Handbook"
    assert info.document == "doc-1"

    hits = await lib.find(collection="tenants/acme/knowledge/handbook", query="policy")
    assert len(hits) == 0

    deleted = await lib.delete(collection="tenants/acme/knowledge/handbook", document="doc-1")
    assert deleted is True
