"""An agent's own account, read only: its chat list (paged, searched, filtered) and what was said in each conversation."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from substrate.runtime import RunSpec, NewEntry
from substrate.types import Actor
from substrate_cloud.monolith.app import app
from substrate_cloud.monolith.database import system_session
from substrate_cloud.monolith.models import Agent
from substrate_cloud.monolith.services.agents import pairs
from substrate_cloud.monolith.services.groups.service import member_actor

from test_scheduled_notifications import session


async def _wipe(tenant: str) -> None:
    store = app.state.ctx.runtime.store
    async with app.state.session_factory() as db:
        await db.execute(text("SELECT set_config('app.bypass_rls', 'on', false)"))
        for (channel,) in (
            await db.execute(
                text("SELECT 'pair/' || id FROM agent_pairs WHERE tenant_id = :t"),
                {"t": tenant},
            )
        ).all():
            await store.channel_delete(channel)
        for table in ("groups", "agents"):
            await db.execute(
                text(f"DELETE FROM {table} WHERE tenant_id = :t"), {"t": tenant}
            )
        await db.commit()


async def _agents(tenant: str, count: int, *, prefix: str = "Contact") -> list[Agent]:
    """Agents made straight in the database: the per-user cap on agents is the API's, and this is about how a long list reads."""
    made = [
        Agent(
            id=uuid.uuid4(),
            tenant_id=tenant,
            user_identifier="u1",
            name=f"{prefix} {i:03d}",
            role="r",
            instructions="",
        )
        for i in range(count)
    ]
    async with system_session(app.state.session_factory) as db:
        db.add_all(made)
        await db.commit()
    return made


async def _talk(
    tenant: str, asker: Agent, target: Agent, *, when: datetime, times: int = 1
) -> uuid.UUID:
    store = app.state.ctx.runtime.store
    async with system_session(app.state.session_factory) as db:
        pair = await pairs.get_or_create_pair(
            db, store, tenant_id=tenant, user_id="u1", asker=asker.id, target=target.id
        )
        for n in range(times):
            thread = f"{uuid.uuid4()}"
            await pairs.record(
                db,
                store,
                pair,
                speaker=asker.id,
                text=f"{asker.name} asks {n}",
                thread_id=thread,
                part="ask",
                status="working",
                asked=True,
            )
            await pairs.record(
                db,
                store,
                pair,
                speaker=target.id,
                text=f"{target.name} answers {n}",
                thread_id=thread,
                part="answer",
                status="done",
            )
        pair.last_at = when
        await db.commit()
        return pair.id


@pytest.mark.requires_postgres
async def test_two_agents_are_one_pair_whoever_asks_and_each_line_is_said_once():
    async with session() as c:
        try:
            a, b = await _agents(c.tenant, 2)
            store = app.state.ctx.runtime.store
            async with system_session(app.state.session_factory) as db:
                one = await pairs.get_or_create_pair(
                    db, store, tenant_id=c.tenant, user_id="u1", asker=a.id, target=b.id
                )
                two = await pairs.get_or_create_pair(
                    db, store, tenant_id=c.tenant, user_id="u1", asker=b.id, target=a.id
                )
                assert one.id == two.id and one.agent_a < one.agent_b
                await pairs.record(
                    db,
                    store,
                    one,
                    speaker=a.id,
                    text="vault?",
                    thread_id="t1",
                    part="ask",
                    status="working",
                    asked=True,
                )
                await pairs.record(
                    db,
                    store,
                    one,
                    speaker=a.id,
                    text="vault?",
                    thread_id="t1",
                    part="ask",
                    status="working",
                )  # a repeat
                await pairs.record(
                    db,
                    store,
                    one,
                    speaker=b.id,
                    text="Lisbon",
                    thread_id="t1",
                    part="answer",
                    status="done",
                )
                await db.commit()
                entries = await store.channel_read(one.channel)
                assert [(e.sender.key == str(a.id), e.text) for e in entries] == [
                    (True, "vault?"),
                    (False, "Lisbon"),
                ]
                assert (
                    one.exchanges == 1
                    and one.last_text == "Lisbon"
                    and one.last_sender == b.id
                )
                assert (
                    await store.channel_members(one.channel) == []
                )  # a record: nobody in it is woken
        finally:
            await _wipe(c.tenant)


@pytest.mark.requires_postgres
async def test_an_agent_with_a_hundred_contacts_is_a_few_pages_newest_first_that_can_be_searched_and_filtered():
    async with session() as c:
        try:
            viewed, *others = await _agents(c.tenant, 101)
            now = datetime.now(timezone.utc)
            for i, other in enumerate(
                others
            ):  # the first contact is the oldest, the last the newest
                await _talk(
                    c.tenant,
                    viewed if i % 2 else other,
                    other if i % 2 else viewed,
                    when=now - timedelta(hours=len(others) - i),
                )

            seen: list[dict] = []
            cursor = None
            pages = 0
            while True:
                params = {"limit": 30, **({"before": cursor} if cursor else {})}
                body = (
                    await c.get(f"/agents/{viewed.id}/view/chats", params=params)
                ).json()
                seen += body["items"]
                pages += 1
                cursor = body["next"]
                if cursor is None:
                    break
            assert (
                pages == 4 and len(seen) == 100 and len({r["key"] for r in seen}) == 100
            )
            assert [r["name"] for r in seen[:2]] == [
                "Contact 100",
                "Contact 099",
            ]  # newest first
            assert [r["at"] for r in seen] == sorted(
                (r["at"] for r in seen), reverse=True
            )
            assert all(r["kind"] == "agent" and r["preview"] for r in seen)

            found = (
                await c.get(
                    f"/agents/{viewed.id}/view/chats", params={"q": "contact 05"}
                )
            ).json()
            assert sorted(r["name"] for r in found["items"]) == [
                f"Contact 05{d}" for d in range(10)
            ]
            assert (
                await c.get(f"/agents/{viewed.id}/view/chats", params={"q": "nobody"})
            ).json() == {"items": [], "next": None}
            assert (
                await c.get(
                    f"/agents/{viewed.id}/view/chats", params={"kind": "groups"}
                )
            ).json()["items"] == []
            # A stranger's agent is not here at all.
            assert (
                await c.get(f"/agents/{uuid.uuid4()}/view/chats")
            ).status_code == 404
        finally:
            await _wipe(c.tenant)


@pytest.mark.requires_postgres
async def test_groups_and_the_chat_with_you_are_rows_too_and_open_as_read_only_conversations():
    async with session() as c:
        try:
            scout = (await c.post("/agents", json={"name": "Scout"})).json()
            quill = (await c.post("/agents", json={"name": "Quill"})).json()
            group = (
                await c.post(
                    "/groups",
                    json={
                        "name": "Trip",
                        "members": [{"agent_id": scout["id"], "mode": "muted"}],
                    },
                )
            ).json()
            store = app.state.ctx.runtime.store
            channel = f"group/{group['id']}"
            await store.channel_append(
                channel,
                sender=member_actor(scout["id"], group["id"]),
                text="Kyoto is lovely in April.",
                fresh=True,
            )

            thread = (await c.post(f"/agents/{scout['id']}/thread")).json()["id"]
            run = await store.create_run(
                RunSpec(
                    agent=Actor(type="agent", key="s"),
                    tenant=c.tenant,
                    thread_id=thread,
                )
            )
            await store.annotate(
                run.run_id,
                [
                    NewEntry(kind=k, payload=p)
                    for k, p in [
                        ("user.message", {"text": "hello Scout"}),
                        ("assistant.message", {"text": "hello!"}),
                    ]
                ],
            )

            async with system_session(app.state.session_factory) as db:
                from substrate_cloud.monolith.models import Agent as A

                s, q = (
                    await db.get(A, uuid.UUID(scout["id"])),
                    await db.get(A, uuid.UUID(quill["id"])),
                )
                pair = await pairs.get_or_create_pair(
                    db, store, tenant_id=c.tenant, user_id="u1", asker=s.id, target=q.id
                )
                await pairs.record(
                    db,
                    store,
                    pair,
                    speaker=s.id,
                    text="polish this",
                    thread_id="t",
                    part="ask",
                    status="working",
                    asked=True,
                )
                await pairs.record(
                    db,
                    store,
                    pair,
                    speaker=q.id,
                    text="Polished.",
                    thread_id="t",
                    part="answer",
                    status="done",
                )
                await db.commit()

            rows = (await c.get(f"/agents/{scout['id']}/view/chats")).json()["items"]
            assert {r["kind"] for r in rows} == {"you", "agent", "group"}
            by_kind = {r["kind"]: r for r in rows}
            assert (
                by_kind["group"]["preview"] == "Kyoto is lovely in April."
                and by_kind["group"]["last_sender"] == "Scout"
            )
            assert (
                by_kind["agent"]["name"] == "Quill"
                and by_kind["agent"]["last_sender"] == "Quill"
            )
            assert [
                r["kind"]
                for r in (
                    await c.get(
                        f"/agents/{scout['id']}/view/chats", params={"kind": "agents"}
                    )
                ).json()["items"]
            ] == ["agent"]
            assert [
                r["kind"]
                for r in (
                    await c.get(
                        f"/agents/{scout['id']}/view/chats", params={"kind": "groups"}
                    )
                ).json()["items"]
            ] == ["group"]
            assert [
                r["name"]
                for r in (
                    await c.get(
                        f"/agents/{scout['id']}/view/chats", params={"q": "tri"}
                    )
                ).json()["items"]
            ] == ["Trip"]

            def read(key: str, **params):
                return c.get(
                    f"/agents/{scout['id']}/view/chats/{key}/messages", params=params
                )

            chat = (await read(by_kind["agent"]["key"])).json()["chat"]
            assert (chat["kind"], chat["name"]) == ("agent", "Quill") and (
                await read(by_kind["group"]["key"])
            ).json()["chat"]["name"] == "Trip"
            you = (await read("you")).json()["entries"]
            assert [(e["sender"], e["text"], e["from_user"]) for e in you] == [
                ("You", "hello Scout", False),
                ("Scout", "hello!", True),
            ]
            both = (await read(by_kind["agent"]["key"])).json()["entries"]
            assert [(e["sender"], e["text"], e["from_user"]) for e in both] == [
                ("Scout", "polish this", True),
                ("Quill", "Polished.", False),
            ]
            talk = (await read(by_kind["group"]["key"])).json()["entries"]
            assert [(e["sender"], e["text"], e["from_user"]) for e in talk] == [
                ("Scout", "Kyoto is lovely in April.", True)
            ]
            # Quill is not in the group, and Scout is not in Quill's pair with anyone else: not theirs to read.
            assert (
                await c.get(
                    f"/agents/{quill['id']}/view/chats/{by_kind['group']['key']}/messages"
                )
            ).status_code == 404
            assert (await read("pair-" + str(uuid.uuid4()))).status_code == 404
            assert (await read("nonsense")).status_code == 404
        finally:
            await _wipe(c.tenant)


@pytest.mark.requires_postgres
async def test_a_long_conversation_is_read_a_page_at_a_time_back_to_the_start():
    async with session() as c:
        try:
            a, b = await _agents(c.tenant, 2)
            pair_id = await _talk(
                c.tenant, a, b, when=datetime.now(timezone.utc), times=60
            )  # 120 lines
            key = f"pair-{pair_id}"
            url = f"/agents/{a.id}/view/chats/{key}/messages"
            latest = (await c.get(url, params={"limit": 50})).json()
            assert len(latest["entries"]) == 50 and latest["has_more"]
            assert latest["entries"][-1]["text"] == f"{b.name} answers 59"
            older = (
                await c.get(
                    url, params={"limit": 50, "before": latest["entries"][0]["seq"]}
                )
            ).json()
            assert len(older["entries"]) == 50 and older["has_more"]
            first = (
                await c.get(
                    url, params={"limit": 50, "before": older["entries"][0]["seq"]}
                )
            ).json()
            assert (
                len(first["entries"]) == 20
                and not first["has_more"]
                and first["entries"][0]["text"] == f"{a.name} asks 0"
            )
            seqs = [
                e["seq"] for page in (first, older, latest) for e in page["entries"]
            ]
            assert seqs == list(range(120))  # nothing missed or repeated across pages
        finally:
            await _wipe(c.tenant)


def test_there_is_nothing_in_an_agents_account_to_write_to():
    """Every route of the view reads and none writes (checked on the routes themselves, not by trying each verb: a wrong verb is the server's 405)."""
    paths = {
        path: set(ops)
        for path, ops in app.openapi()["paths"].items()
        if "/view/" in path
    }
    assert paths, "the view has no routes"
    assert all(methods == {"get"} for methods in paths.values()), paths


@pytest.mark.requires_postgres
async def test_deleting_an_agent_takes_its_pairs_and_what_was_said_in_them():
    async with session() as c:
        try:
            a, b = await _agents(c.tenant, 2)
            pair_id = await _talk(c.tenant, a, b, when=datetime.now(timezone.utc))
            store = app.state.ctx.runtime.store
            assert await store.channel_read(f"pair/{pair_id}")
            assert (await c.delete(f"/agents/{b.id}")).status_code == 204
            assert await store.channel_read(f"pair/{pair_id}") == []
            assert (await c.get(f"/agents/{a.id}/view/chats")).json()["items"] == []
        finally:
            await _wipe(c.tenant)


@pytest.mark.requires_postgres
async def test_the_feed_follows_a_viewed_agents_pairs_only_when_you_ask_and_only_if_it_is_yours():
    from substrate_cloud.monolith.security.deps import AuthClaims
    from substrate_cloud.realtime.chats import chats_for

    async with session() as c:
        try:
            a, b = await _agents(c.tenant, 2)
            pair_id = await _talk(c.tenant, a, b, when=datetime.now(timezone.utc))
            me = AuthClaims(sub="u1", tenant_id=c.tenant)
            async with system_session(app.state.session_factory) as db:
                assert await chats_for(db, me) == {}
                watched = await chats_for(db, me, a.id)
                chat = watched[f"pair/{pair_id}"]
                assert chat.group_id == f"pair-{pair_id}" and set(
                    chat.names.values()
                ) == {a.name, b.name}
                assert (
                    await chats_for(db, me, uuid.uuid4()) == {}
                )  # not an agent of yours
                assert (
                    await chats_for(
                        db, AuthClaims(sub="someone-else", tenant_id=c.tenant), a.id
                    )
                    == {}
                )
        finally:
            await _wipe(c.tenant)
