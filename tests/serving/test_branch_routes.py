"""Integration tests for /threads/{id}/branches endpoints.

Tests listing branches, forking, retrieving details, ancestry message resolution,
compaction checkpoints, and tenant/owner isolation.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from substrate.kernel.core.content import ChatMessage, TextBlock
from substrate.kernel.storage.history import HistoryCheckpoint, MessageNode
from substrate.serving.monolith.app import app
from substrate.serving.monolith.models import Thread, User
from substrate.serving.monolith.security.deps import get_current_user
from substrate.serving.shared.auth.claims import AuthClaims

TENANT = "test-branch-tenant"
OTHER_TENANT = "other-tenant"


def _claims_for(user_id: str, tenant_id: str = TENANT) -> AuthClaims:
    return AuthClaims(sub=user_id, tenant_id=tenant_id)


@asynccontextmanager
async def _bypass_session():
    session_factory = app.state.session_factory
    async with session_factory() as db:
        await db.execute(text("SELECT set_config('app.bypass_rls', 'on', false)"))
        yield db


@asynccontextmanager
async def _registered_user(user_id: str | None = None, tenant_id: str = TENANT):
    uid = uuid.UUID(user_id) if user_id else uuid.uuid4()
    async with _bypass_session() as db:
        db.add(User(id=uid, identifier=f"test-{uid}"))
        await db.commit()
    try:
        yield str(uid)
    finally:
        async with _bypass_session() as db:
            row = await db.get(User, uid)
            if row is not None:
                await db.delete(row)
                await db.commit()


@asynccontextmanager
async def _registered_thread(thread_id: str, *, owner: str, tenant_id: str = TENANT):
    tid = uuid.UUID(thread_id)
    async with _bypass_session() as db:
        db.add(
            Thread(
                id=tid,
                name="test thread",
                user_identifier=owner,
                tenant_id=tenant_id,
            )
        )
        await db.commit()
    try:
        yield thread_id
    finally:
        async with _bypass_session() as db:
            row = await db.get(Thread, tid)
            if row is not None:
                await db.delete(row)
                await db.commit()


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_list_and_fork_branches():
    async with app.router.lifespan_context(app):
        async with _registered_user() as user_id:
            claims = _claims_for(user_id)
            thread_id = str(uuid.uuid4())
            async with _registered_thread(thread_id, owner=user_id):
                app.dependency_overrides[get_current_user] = lambda: claims
                try:
                    transport = ASGITransport(app=app)
                    async with AsyncClient(transport=transport, base_url="http://test") as client:
                        # 1. List branches -> should return at least "main"
                        res = await client.get(f"/threads/{thread_id}/branches")
                        assert res.status_code == 200, res.text
                        branches = res.json()
                        assert len(branches) >= 1
                        assert any(b["id"] == "main" for b in branches)

                        # 2. Fork branch from main -> feature-1
                        fork_res = await client.post(
                            f"/threads/{thread_id}/branches/fork",
                            json={
                                "source_branch_id": "main",
                                "new_branch_id": "feature-1",
                            },
                        )
                        assert fork_res.status_code == 201, fork_res.text
                        fork_data = fork_res.json()
                        assert fork_data["id"] == "feature-1"
                        assert fork_data["session_id"] == thread_id

                        # 3. Duplicate fork should conflict 409
                        dup_res = await client.post(
                            f"/threads/{thread_id}/branches/fork",
                            json={
                                "source_branch_id": "main",
                                "new_branch_id": "feature-1",
                            },
                        )
                        assert dup_res.status_code == 409

                        # 4. List branches now has main and feature-1
                        res = await client.get(f"/threads/{thread_id}/branches")
                        assert res.status_code == 200
                        branch_ids = [b["id"] for b in res.json()]
                        assert "main" in branch_ids
                        assert "feature-1" in branch_ids

                        # 5. Get branch details
                        detail_res = await client.get(f"/threads/{thread_id}/branches/feature-1")
                        assert detail_res.status_code == 200
                        assert detail_res.json()["id"] == "feature-1"

                        # 6. Non-existent branch
                        # 6. Rename branch
                        rename_res = await client.patch(
                            f"/threads/{thread_id}/branches/feature-1",
                            json={"name": "My Great Feature"},
                        )
                        assert rename_res.status_code == 200
                        rename_data = rename_res.json()
                        assert rename_data["id"] == "feature-1"
                        assert rename_data["name"] == "My Great Feature"

                        # 7. Non-existent branch
                        missing_res = await client.get(f"/threads/{thread_id}/branches/non-existent")
                        assert missing_res.status_code == 404
                finally:
                    app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_branch_messages_ancestry():
    async with app.router.lifespan_context(app):
        async with _registered_user() as user_id:
            claims = _claims_for(user_id)
            thread_id = str(uuid.uuid4())
            async with _registered_thread(thread_id, owner=user_id):
                app.dependency_overrides[get_current_user] = lambda: claims
                try:
                    transport = ASGITransport(app=app)
                    async with AsyncClient(transport=transport, base_url="http://test") as client:
                        # Initially empty
                        msg_res = await client.get(f"/threads/{thread_id}/branches/main/messages")
                        assert msg_res.status_code == 200
                        assert msg_res.json() == []

                        # Append two nodes to main branch in history
                        history = app.state.ctx.history
                        await history.ensure_branch(thread_id, "main")
                        node1_id = f"msg1-{uuid.uuid4().hex[:8]}"
                        node2_id = f"msg2-{uuid.uuid4().hex[:8]}"
                        node1 = MessageNode(
                            id=node1_id,
                            parent_id=None,
                            session_id=thread_id,
                            payload=ChatMessage(role="user", content=[TextBlock(text="hello")]),
                        )
                        await history.append_and_advance(node1, "main", expected_head_id=None)

                        node2 = MessageNode(
                            id=node2_id,
                            parent_id=node1_id,
                            session_id=thread_id,
                            payload=ChatMessage(role="assistant", content=[TextBlock(text="hi there")]),
                        )
                        await history.append_and_advance(node2, "main", expected_head_id=node1_id)

                        # Now check messages
                        res = await client.get(f"/threads/{thread_id}/branches/main/messages")
                        assert res.status_code == 200
                        messages = res.json()
                        assert len(messages) == 2
                        assert messages[0]["id"] == node1_id
                        assert messages[0]["text"] == "hello"
                        assert messages[1]["id"] == node2_id
                        assert messages[1]["text"] == "hi there"

                        # Fork branch from msg-1
                        fork_res = await client.post(
                            f"/threads/{thread_id}/branches/fork",
                            json={
                                "source_branch_id": "main",
                                "new_branch_id": "alternate",
                                "fork_from_message_id": node1_id,
                            },
                        )
                        assert fork_res.status_code == 201

                        # The alternate branch should only see msg-1 in its resolved messages
                        alt_res = await client.get(f"/threads/{thread_id}/branches/alternate/messages")
                        assert alt_res.status_code == 200
                        alt_msgs = alt_res.json()
                        assert len(alt_msgs) == 1
                        assert alt_msgs[0]["id"] == node1_id
                finally:
                    app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_checkpoints_endpoints():
    async with app.router.lifespan_context(app):
        async with _registered_user() as user_id:
            claims = _claims_for(user_id)
            thread_id = str(uuid.uuid4())
            async with _registered_thread(thread_id, owner=user_id):
                app.dependency_overrides[get_current_user] = lambda: claims
                try:
                    transport = ASGITransport(app=app)
                    async with AsyncClient(transport=transport, base_url="http://test") as client:
                        # Append a node first so we have an anchor
                        history = app.state.ctx.history
                        anchor_id = f"anchor-{uuid.uuid4().hex[:8]}"
                        node = MessageNode(
                            id=anchor_id,
                            parent_id=None,
                            session_id=thread_id,
                            payload=ChatMessage(role="user", content=[TextBlock(text="anchor test")]),
                        )
                        await history.append_node(node)

                        # Save checkpoint
                        cp_res = await client.post(
                            f"/threads/{thread_id}/branches/main/checkpoints",
                            json={
                                "anchor_message_id": anchor_id,
                                "summary": "Summary of turn 1",
                                "state": {"step": 1},
                            },
                        )
                        assert cp_res.status_code == 201
                        cp_data = cp_res.json()
                        assert cp_data["anchor_message_id"] == anchor_id
                        assert cp_data["summary"] == "Summary of turn 1"

                        # List checkpoints
                        list_res = await client.get(f"/threads/{thread_id}/branches/main/checkpoints")
                        assert list_res.status_code == 200
                        checkpoints = list_res.json()
                        assert len(checkpoints) >= 1
                        assert any(c["anchor_message_id"] == anchor_id for c in checkpoints)
                finally:
                    app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_tenant_and_owner_isolation():
    async with app.router.lifespan_context(app):
        async with _registered_user() as owner_id, _registered_user() as stranger_id:
            owner_claims = _claims_for(owner_id, tenant_id=TENANT)
            stranger_claims = _claims_for(stranger_id, tenant_id=TENANT)
            cross_tenant_claims = _claims_for(owner_id, tenant_id=OTHER_TENANT)

            thread_id = str(uuid.uuid4())
            async with _registered_thread(thread_id, owner=owner_id, tenant_id=TENANT):
                transport = ASGITransport(app=app)

                # Stranger in same tenant -> 404
                app.dependency_overrides[get_current_user] = lambda: stranger_claims
                try:
                    async with AsyncClient(transport=transport, base_url="http://test") as client:
                        res = await client.get(f"/threads/{thread_id}/branches")
                        assert res.status_code == 404
                finally:
                    app.dependency_overrides.pop(get_current_user, None)

                # Same user in different tenant -> 404
                app.dependency_overrides[get_current_user] = lambda: cross_tenant_claims
                try:
                    async with AsyncClient(transport=transport, base_url="http://test") as client:
                        res = await client.get(f"/threads/{thread_id}/branches")
                        assert res.status_code == 404
                finally:
                    app.dependency_overrides.pop(get_current_user, None)
