"""How a group member attends: it is quiet until addressed, then follows the conversation; it takes a cheap look before a full turn at what is not for it;
it does not attend to chatter while it is busy elsewhere; and what the agents may spend in a group is the user's to set."""

from __future__ import annotations


import pytest
from sqlalchemy import text

from substrate.types import Actor
from substrate_cloud.monolith.app import app
from substrate_cloud.monolith.services.groups.member import build_member
from substrate_cloud.monolith.services.groups.service import member_actor

from test_scheduled_notifications import session


async def _wipe(tenant: str) -> None:
    async with app.state.session_factory() as db:
        await db.execute(text("SELECT set_config('app.bypass_rls', 'on', false)"))
        for table in ("groups", "agents"):
            await db.execute(text(f"DELETE FROM {table} WHERE tenant_id = :t"), {"t": tenant})
        await db.commit()


@pytest.mark.requires_postgres
async def test_a_new_member_listens_for_what_is_for_it_unless_told_otherwise():
    async with session() as c:
        try:
            scout = (await c.post("/agents", json={"name": "Scout"})).json()
            quill = (await c.post("/agents", json={"name": "Quill"})).json()
            made = (await c.post("/groups", json={"name": "Trip", "members": [{"agent_id": scout["id"]}, {"agent_id": quill["id"], "mode": "all"}]})).json()
            assert {m["name"]: m["mode"] for m in made["members"]} == {"Scout": "mentions", "Quill": "all"}
            other = (await c.post("/agents", json={"name": "Max"})).json()
            added = (await c.post(f"/groups/{made['id']}/members", json={"agent_id": other["id"]})).json()
            assert {m["name"]: m["mode"] for m in added["members"]}["Max"] == "mentions"
        finally:
            await _wipe(c.tenant)


@pytest.mark.requires_postgres
async def test_a_member_knows_who_does_what_has_a_cheap_look_and_is_unavailable_while_busy_elsewhere():
    async with session() as c:
        try:
            scout = (await c.post("/agents", json={"name": "Scout", "role": "Researcher"})).json()
            quill = (await c.post("/agents", json={"name": "Quill", "role": "Editor"})).json()
            trip = (await c.post("/groups", json={"name": "Trip", "members": [{"agent_id": scout["id"]}, {"agent_id": quill["id"]}]})).json()
            kitchen = (await c.post("/groups", json={"name": "Kitchen", "members": [{"agent_id": scout["id"]}]})).json()
            ctx = app.state.ctx

            built = await build_member(ctx, member_actor(scout["id"], trip["id"]))
            config = built._config
            assert config.roles == {"Scout": "Researcher", "Quill": "Editor"}
            assert config.triage is not None

            working: list[Actor] = []

            async def fake_working(actors):
                return [a for a in actors if a in working]

            real = ctx.runtime.store.working
            ctx.runtime.store.working = fake_working
            try:
                assert await config.availability() is True
                working.append(member_actor(scout["id"], kitchen["id"]))  # busy in another group
                assert await config.availability() is False
                working.clear()
                thread = (await c.post(f"/agents/{scout['id']}/thread")).json()["id"]
                working.append(Actor("assistant", thread))  # or in a chat with the user
                assert await build_member(ctx, member_actor(scout["id"], trip["id"])) is not None
                assert await (await build_member(ctx, member_actor(scout["id"], trip["id"])))._config.availability() is False
            finally:
                ctx.runtime.store.working = real
        finally:
            await _wipe(c.tenant)


@pytest.mark.requires_postgres
async def test_what_the_agents_may_spend_and_how_long_they_may_talk_unattended_is_set_and_shown():
    async with session() as c:
        try:
            scout = (await c.post("/agents", json={"name": "Scout"})).json()
            group = (await c.post("/groups", json={"name": "Trip", "members": [{"agent_id": scout["id"]}]})).json()
            assert group["budget_usd"] is None and group["breaker"] == 40 and group["cost_usd"] == 0
            assert group["members"][0]["tokens_used"] == 0 and group["members"][0]["cost_usd"] == 0

            changed = (await c.patch(f"/groups/{group['id']}", json={"budget_usd": 2.5, "breaker": 12})).json()
            assert changed["budget_usd"] == 2.5 and changed["breaker"] == 12
            assert (await c.patch(f"/groups/{group['id']}", json={"budget_usd": 0})).json()["budget_usd"] is None  # 0 removes it
            assert (await c.patch(f"/groups/{group['id']}", json={"breaker": 1})).status_code == 422
            assert (await c.patch(f"/groups/{group['id']}", json={"budget_usd": -1})).status_code == 422

            # the dollar budget sits beside the token cap on the one account: setting one keeps the other
            await c.patch(f"/groups/{group['id']}", json={"budget_usd": 1.0})
            kept = (await c.get(f"/groups/{group['id']}")).json()
            assert kept["token_cap"] == 1_000_000 and kept["budget_usd"] == 1.0
        finally:
            await _wipe(c.tenant)
