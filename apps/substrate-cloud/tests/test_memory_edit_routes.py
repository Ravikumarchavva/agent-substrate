"""POST/PATCH /me/memories: add and reword what the assistant remembers. A real folder store (SQLite), no server."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from substrate.stores import MemoryNamespace, MemoryRecord, Store
from substrate_cloud.monolith.dependencies import get_ctx
from substrate_cloud.monolith.routes import memory as routes
from substrate_cloud.monolith.security.deps import get_current_user
from substrate_cloud.shared.auth.claims import AuthClaims

TENANT = "acme"


@pytest.fixture
async def store(tmp_path):
    store = Store.at(tmp_path / "s")
    await store.start()
    yield store
    await store.aclose()


def client_for(store, user: str) -> AsyncClient:
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_ctx] = lambda: SimpleNamespace(
        long_term_memory=store.memory
    )
    app.dependency_overrides[get_current_user] = lambda: AuthClaims(
        sub=user, tenant_id=TENANT
    )
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_a_user_adds_a_memory_and_sees_it_listed(store):
    async with client_for(store, "u1") as c:
        r = await c.post("/me/memories", json={"content": "  I prefer metric   units "})
        assert r.status_code == 201 and r.json()["content"] == "I prefer metric units"
        assert [m["content"] for m in (await c.get("/me/memories")).json()] == [
            "I prefer metric units"
        ]


async def test_the_same_memory_twice_and_an_empty_one_are_refused(store):
    async with client_for(store, "u1") as c:
        await c.post("/me/memories", json={"content": "Call me Sam"})
        assert (
            await c.post("/me/memories", json={"content": "call me sam"})
        ).status_code == 409
        assert (
            await c.post("/me/memories", json={"content": "   "})
        ).status_code == 422
        assert (
            await c.post("/me/memories", json={"content": "x" * 501})
        ).status_code == 422


async def test_there_is_a_cap_so_every_memory_stays_visible_and_deletable(
    store, monkeypatch
):
    monkeypatch.setattr(routes, "MAX_MEMORIES", 2)
    async with client_for(store, "u1") as c:
        await c.post("/me/memories", json={"content": "one"})
        await c.post("/me/memories", json={"content": "two"})
        r = await c.post("/me/memories", json={"content": "three"})
        assert r.status_code == 409 and "Delete one" in r.json()["detail"]


async def test_rewording_keeps_the_same_memory_and_changes_its_text(store):
    async with client_for(store, "u1") as c:
        made = (
            await c.post("/me/memories", json={"content": "I live in Leeds"})
        ).json()
        r = await c.patch(
            f"/me/memories/{made['id']}", json={"content": "I live in York"}
        )
        assert r.status_code == 200 and r.json() == {
            "id": made["id"],
            "content": "I live in York",
        }
        assert [m["content"] for m in (await c.get("/me/memories")).json()] == [
            "I live in York"
        ]


async def test_nobody_can_reword_another_users_or_a_tenant_wide_memory(store):
    other = MemoryRecord.from_text(
        "u2's secret", namespace=MemoryNamespace(tenant_id=TENANT, user_id="u2")
    )
    shared = MemoryRecord.from_text(
        "Company holiday is 1 May", namespace=MemoryNamespace(tenant_id=TENANT)
    )
    await store.memory.save(other)
    await store.memory.save(shared)
    async with client_for(store, "u1") as c:
        for record in (other, shared):
            r = await c.patch(f"/me/memories/{record.id}", json={"content": "changed"})
            assert r.status_code == 404
    assert (
        await store.memory.get(
            MemoryNamespace(tenant_id=TENANT, user_id="u2"), other.id
        )
    ).text == "u2's secret"
