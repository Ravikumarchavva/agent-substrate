"""Pin, archive, export and share on real Postgres (the threads table is ORM/JSONB), through the real app, as a throwaway tenant."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import text

from substrate.stores import MemoryNamespace
from httpx import ASGITransport, AsyncClient

from substrate_cloud.monolith.app import app
from substrate_cloud.monolith.security.deps import get_current_user
from substrate_cloud.shared.auth.claims import AuthClaims

pytestmark = pytest.mark.requires_postgres


def claims(user: str, tenant: str) -> AuthClaims:
    return AuthClaims(sub=user, tenant_id=tenant)


@asynccontextmanager
async def session():
    """The real app, signed in as a new user of a new tenant. (A context manager, not a fixture: the lifespan's task group must be
    entered and left in the same task.)"""
    async with app.router.lifespan_context(app):
        suffix = uuid.uuid4().hex[:10]
        tenant, user = f"threadtest-{suffix}", f"user-{suffix}"
        app.dependency_overrides[get_current_user] = lambda: claims(user, tenant)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            c.tenant, c.user = tenant, user
            try:
                yield c
            finally:
                app.dependency_overrides.pop(get_current_user, None)
                # the threads are real rows in the dev database: remove them (their shares go with them)
                async with app.state.session_factory() as db:
                    await db.execute(
                        text("SELECT set_config('app.bypass_rls', 'on', false)")
                    )
                    await db.execute(
                        text("DELETE FROM threads WHERE tenant_id = :t"), {"t": tenant}
                    )
                    await db.commit()
                memory = app.state.ctx.long_term_memory
                if memory is not None:
                    await memory.erase(MemoryNamespace(tenant_id=tenant))


async def make(c: AsyncClient, name: str) -> str:
    r = await c.post("/threads", json={"name": name})
    assert r.status_code == 201
    return r.json()["id"]


async def names(c: AsyncClient, **params) -> list[str]:
    r = await c.get("/threads", params=params)
    assert r.status_code == 200
    return [t["name"] for t in r.json()]


async def test_pinned_threads_sort_first_and_archived_ones_leave_the_main_list():
    async with session() as client:
        a, b, c_ = (
            await make(client, "A"),
            await make(client, "B"),
            await make(client, "C"),
        )
        assert await names(client) == ["C", "B", "A"]  # newest first

        await client.patch(f"/threads/{a}", json={"pinned": True})
        assert await names(client) == [
            "A",
            "C",
            "B",
        ]  # pinned first; pinning did not reorder the rest

        await client.patch(f"/threads/{b}", json={"archived": True})
        assert await names(client) == ["A", "C"]
        assert await names(client, archived=True) == ["B"]

        await client.patch(
            f"/threads/{a}", json={"archived": True}
        )  # archiving also unpins
        got = (await client.get("/threads", params={"archived": True})).json()
        assert {t["name"] for t in got} == {"A", "B"} and all(
            t["pinned_at"] is None for t in got
        )

        await client.patch(f"/threads/{b}", json={"archived": False})
        assert "B" in await names(client)
        _ = c_


async def test_threads_come_a_page_at_a_time():
    async with session() as client:
        for n in range(5):
            await make(client, f"T{n}")
        first = await names(client, limit=2)
        second = await names(client, limit=2, offset=2)
        assert first == ["T4", "T3"] and second == ["T2", "T1"]
        assert (await client.get("/threads", params={"limit": 1000})).status_code == 422


async def test_export_downloads_markdown_and_json_for_the_owner_only():
    async with session() as client:
        tid = await make(client, "Q1 report")
        md = await client.get(f"/threads/{tid}/export")
        assert md.status_code == 200 and md.text.startswith("# Q1 report")
        assert "q1-report.md" in md.headers["content-disposition"]
        js = await client.get(f"/threads/{tid}/export", params={"format": "json"})
        assert js.json()["title"] == "Q1 report"
        assert (
            await client.get(f"/threads/{tid}/export", params={"format": "pdf"})
        ).status_code == 422

        app.dependency_overrides[get_current_user] = lambda: claims(
            "someone-else", client.tenant
        )
        assert (await client.get(f"/threads/{tid}/export")).status_code == 404


async def test_a_share_link_is_public_read_only_and_ends_when_stopped():
    async with session() as client:
        tid = await make(client, "Shared chat")
        assert (await client.get(f"/threads/{tid}/share")).json() is None
        token = (await client.post(f"/threads/{tid}/share")).json()["token"]
        assert (await client.post(f"/threads/{tid}/share")).json()[
            "token"
        ] == token  # idempotent
        assert (await client.get(f"/threads/{tid}/share")).json() == {"token": token}

        # anyone with the link, no sign-in
        app.dependency_overrides.pop(get_current_user, None)
        seen = await client.get(f"/shared/{token}")
        assert seen.status_code == 200 and seen.json()["title"] == "Shared chat"
        assert (await client.get("/shared/not-a-real-token")).status_code == 404

        app.dependency_overrides[get_current_user] = lambda: claims(
            client.user, client.tenant
        )
        assert (await client.delete(f"/threads/{tid}/share")).status_code == 204
        app.dependency_overrides.pop(get_current_user, None)
        assert (await client.get(f"/shared/{token}")).status_code == 404


async def test_deleting_a_conversation_ends_its_share_link():
    async with session() as client:
        tid = await make(client, "Gone soon")
        token = (await client.post(f"/threads/{tid}/share")).json()["token"]
        assert (await client.delete(f"/threads/{tid}")).status_code == 204
        app.dependency_overrides.pop(get_current_user, None)
        assert (await client.get(f"/shared/{token}")).status_code == 404


async def test_nobody_else_can_share_or_unshare_your_conversation():
    async with session() as client:
        tid = await make(client, "Mine")
        app.dependency_overrides[get_current_user] = lambda: claims(
            "intruder", client.tenant
        )
        assert (await client.post(f"/threads/{tid}/share")).status_code == 404
        assert (await client.delete(f"/threads/{tid}/share")).status_code == 404
        assert (await client.get("/threads/search", params={"q": "mine"})).json() == []


async def test_usage_and_data_export_cover_the_callers_own_account():
    async with session() as client:
        tid = await make(client, "Export me")
        await client.post("/me/memories", json={"content": "I like tea"})
        usage = (await client.get("/me/usage", params={"days": 7})).json()
        assert len(usage["days"]) == 7 and usage["today"]["messages"] == 0
        assert (await client.get("/me/usage", params={"days": 500})).status_code == 422

        exported = await client.get("/me/export")
        assert exported.status_code == 200
        assert "my-data.json" in exported.headers["content-disposition"]
        body = exported.json()
        assert [c["title"] for c in body["conversations"]] == ["Export me"]
        assert body["conversations"][0]["id"] == tid
        assert body["memories"] == ["I like tea"]
        # a second export straight away is refused, politely
        again = await client.get("/me/export")
        assert again.status_code == 429 and again.headers["retry-after"]
