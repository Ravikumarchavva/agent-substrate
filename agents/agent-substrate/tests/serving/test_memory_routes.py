"""GET/DELETE /me/memories — the HTTP surface for viewing/deleting facts
MemoryTool.remember() saves. Real Postgres, matching this session's
established pattern for testing durable stores (see test_workspace_routes.py)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from substrate.capabilities.memory.durable_memory_store import DurableMemoryStore
from substrate.kernel.core.identity import AgentId
from substrate.serving.monolith.app import app
from substrate.serving.monolith.security.deps import get_current_user
from substrate.serving.shared.auth.claims import AuthClaims


def _claims_for(user_id: str) -> AuthClaims:
    return AuthClaims(sub=user_id, tenant_id="test-tenant")


@pytest.mark.requires_postgres
async def test_list_memories_returns_only_this_users_facts() -> None:
    async with app.router.lifespan_context(app):
        store: DurableMemoryStore = app.state.ctx.long_term_memory
        user_a, user_b = "memroute-user-a", "memroute-user-b"
        await store.clear(AgentId(type="user", key=user_a))
        await store.clear(AgentId(type="user", key=user_b))
        await store.save(AgentId(type="user", key=user_a), "Always answer in French")
        await store.save(AgentId(type="user", key=user_b), "Not user a's memory")

        app.dependency_overrides[get_current_user] = lambda: _claims_for(user_a)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/me/memories")
                assert resp.status_code == 200
                bodies = resp.json()
                assert len(bodies) == 1
                assert bodies[0]["content"] == "Always answer in French"
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            await store.clear(AgentId(type="user", key=user_a))
            await store.clear(AgentId(type="user", key=user_b))


@pytest.mark.requires_postgres
async def test_delete_memory_removes_it() -> None:
    async with app.router.lifespan_context(app):
        store: DurableMemoryStore = app.state.ctx.long_term_memory
        user_id = "memroute-delete-user"
        await store.clear(AgentId(type="user", key=user_id))
        mem_id = await store.save(AgentId(type="user", key=user_id), "delete me")

        app.dependency_overrides[get_current_user] = lambda: _claims_for(user_id)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.delete(f"/me/memories/{mem_id}")
                assert resp.status_code == 204

                remaining = await client.get("/me/memories")
                assert remaining.json() == []
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            await store.clear(AgentId(type="user", key=user_id))


@pytest.mark.requires_postgres
async def test_delete_memory_owned_by_another_user_is_not_found() -> None:
    """A user must not be able to delete another user's memory by id
    (agent_name is part of the DELETE's WHERE clause, not just the id)."""
    async with app.router.lifespan_context(app):
        store: DurableMemoryStore = app.state.ctx.long_term_memory
        owner, attacker = "memroute-owner", "memroute-attacker"
        await store.clear(AgentId(type="user", key=owner))
        await store.clear(AgentId(type="user", key=attacker))
        mem_id = await store.save(AgentId(type="user", key=owner), "owner's secret")

        app.dependency_overrides[get_current_user] = lambda: _claims_for(attacker)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.delete(f"/me/memories/{mem_id}")
                assert resp.status_code == 404

            assert await store.get(AgentId(type="user", key=owner), mem_id) is not None
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            await store.clear(AgentId(type="user", key=owner))
            await store.clear(AgentId(type="user", key=attacker))


@pytest.mark.requires_postgres
async def test_delete_missing_memory_returns_404() -> None:
    async with app.router.lifespan_context(app):
        app.dependency_overrides[get_current_user] = lambda: _claims_for("no-such-user")
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.delete("/me/memories/does-not-exist")
                assert resp.status_code == 404
        finally:
            app.dependency_overrides.pop(get_current_user, None)
