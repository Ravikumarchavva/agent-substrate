"""Groups: the user and several of their agents in one conversation. Each agent sees every message and chooses whether to reply; contacts
are the agents one may message directly."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import text

from substrate.testing.scripted import ScriptedModel
from substrate_cloud.monolith.app import app
from substrate_cloud.monolith.services.agents.delegation import contacts_for
from substrate_cloud.monolith.services.groups.member import group_instructions
from substrate_cloud.monolith.services.groups.service import member_actor, parse_member

from test_scheduled_notifications import session


def test_a_members_address_round_trips():
    agent, group = uuid.uuid4(), uuid.uuid4()
    assert parse_member(member_actor(agent, group)) == (agent, group)


def test_the_model_is_told_to_stay_silent_with_the_pass_token():
    block = group_instructions("Trip", "Scout", {"user/u": "Ravi", "member/a@g": "Scout", "member/b@g": "Quill"}, "trip")
    assert "Ravi, Quill" in block and "[PASS]" in block and "Scout" in block


async def _wipe(tenant: str) -> None:
    async with app.state.session_factory() as db:
        await db.execute(text("SELECT set_config('app.bypass_rls', 'on', false)"))
        for table in ("groups", "agents"):
            await db.execute(text(f"DELETE FROM {table} WHERE tenant_id = :t"), {"t": tenant})
        await db.commit()


@pytest.mark.requires_postgres
async def test_groups_are_private_validated_and_hold_the_members_chosen():
    async with session() as c:
        try:
            scout = (await c.post("/agents", json={"name": "Scout"})).json()
            quill = (await c.post("/agents", json={"name": "Quill"})).json()
            assert (await c.post("/groups", json={"name": "Trip", "members": []})).status_code == 422
            assert (await c.post("/groups", json={"name": "Trip", "members": [{"agent_id": str(uuid.uuid4())}]})).status_code == 404
            assert (await c.post("/groups", json={"name": "Trip", "members": [{"agent_id": scout["id"], "mode": "loud"}]})).status_code == 422

            made = (await c.post("/groups", json={"name": "Trip", "members": [{"agent_id": scout["id"]}, {"agent_id": quill["id"], "mode": "mentions"}]})).json()
            assert {m["name"]: m["mode"] for m in made["members"]} == {"Scout": "all", "Quill": "mentions"}
            assert [g["id"] for g in (await c.get("/groups")).json()] == [made["id"]]

            changed = (await c.patch(f"/groups/{made['id']}/members/{quill['id']}", json={"mode": "muted"})).json()
            assert {m["name"]: m["mode"] for m in changed["members"]}["Quill"] == "muted"
            assert (await c.delete(f"/groups/{made['id']}/members/{quill['id']}")).status_code == 204
            assert (await c.delete(f"/groups/{made['id']}/members/{scout['id']}")).status_code == 422  # the last one stays

            assert (await c.delete(f"/groups/{made['id']}")).status_code == 204
            assert (await c.get(f"/groups/{made['id']}")).status_code == 404
        finally:
            await _wipe(c.tenant)


@pytest.mark.requires_postgres
async def test_contacts_are_the_callers_own_agents_and_never_the_agent_itself():
    async with session() as c:
        try:
            scout = (await c.post("/agents", json={"name": "Scout"})).json()
            quill = (await c.post("/agents", json={"name": "Quill"})).json()
            put = await c.put(
                f"/agents/{scout['id']}/contacts",
                json={"contacts": [{"agent_id": quill["id"], "note": "Edits my drafts"}, {"agent_id": scout["id"]}]},
            )
            assert [(x["name"], x["note"]) for x in put.json()] == [("Quill", "Edits my drafts")]
            assert (await c.put(f"/agents/{scout['id']}/contacts", json={"contacts": [{"agent_id": str(uuid.uuid4())}]})).status_code == 404
            assert len((await c.get(f"/agents/{scout['id']}/contacts")).json()) == 1
            # What it may message is its contacts and nothing else: Quill has none, and neither is the user's roster handed over.
            ctx = app.state.ctx
            assert [a.name for a in await contacts_for(ctx, c.tenant, "u1", uuid.UUID(scout["id"]))] == ["Quill"]
            assert await contacts_for(ctx, c.tenant, "u1", uuid.UUID(quill["id"])) == []
            assert (await c.put(f"/agents/{scout['id']}/contacts", json={"contacts": []})).json() == []
        finally:
            await _wipe(c.tenant)


@pytest.mark.requires_postgres
async def test_a_message_reaches_every_agent_and_only_one_of_two_answers():
    async with session() as c:
        try:
            answered = []

            def reply(messages):
                # The first agent to look speaks; the second sees that answer in its digest and has nothing to add.
                if answered:
                    return "[PASS]"
                answered.append(1)
                return "Here is what I found."

            app.state.ctx.model_client = ScriptedModel(reply)
            scout = (await c.post("/agents", json={"name": "Scout"})).json()
            quill = (await c.post("/agents", json={"name": "Quill"})).json()
            group = (await c.post("/groups", json={"name": "Trip", "members": [{"agent_id": scout["id"]}, {"agent_id": quill["id"]}]})).json()

            sent = (await c.post(f"/groups/{group['id']}/messages", json={"text": "Where should we go?"})).json()
            assert sent["from_user"] and sent["seq"] == 0

            entries = []
            for _ in range(80):
                await asyncio.sleep(0.5)
                entries = (await c.get(f"/groups/{group['id']}/messages")).json()["entries"]
                if len(entries) >= 2:
                    break
            await asyncio.sleep(4)  # long enough for the other member to look, and to be wrong if it were going to speak
            entries = (await c.get(f"/groups/{group['id']}/messages")).json()["entries"]

            assert len(entries) == 2, entries
            assert entries[1]["sender"] in {"Scout", "Quill"} and not entries[1]["from_user"]
            # (A server running against the same database may pick the run up first, with its own model: only who spoke is asserted.)

            listed = (await c.get("/groups")).json()[0]
            assert listed["unread"] == 1 and listed["last_message"] == entries[1]["text"][:140]
            await c.post(f"/groups/{group['id']}/read", json={"upto": entries[-1]["seq"]})
            assert (await c.get("/groups")).json()[0]["unread"] == 0
        finally:
            await _wipe(c.tenant)


def test_the_contact_note_reaches_the_agent_that_may_ask():
    from substrate_cloud.monolith.services.agents.delegation import AgentRef, AskAgentTool

    tool = AskAgentTool(None, [AgentRef(uuid.uuid4(), "Quill", "Editor", "Ask before publishing")], lambda t: "")
    assert "Quill (Editor: Ask before publishing)" in tool.description


@pytest.mark.requires_postgres
async def test_an_agent_added_later_was_not_there_for_what_came_before():
    async with session() as c:
        try:
            scout = (await c.post("/agents", json={"name": "Scout"})).json()
            quill = (await c.post("/agents", json={"name": "Quill"})).json()
            group = (await c.post("/groups", json={"name": "Trip", "members": [{"agent_id": scout["id"], "mode": "muted"}]})).json()
            await c.post(f"/groups/{group['id']}/messages", json={"text": "Secret plan: Tokyo"})
            await c.post(f"/groups/{group['id']}/members", json={"agent_id": quill["id"], "mode": "muted"})
            store = app.state.ctx.runtime.store
            members = {m.agent.key.split("@")[0]: m.cursor for m in await store.channel_members(f"group/{group['id']}")}
            assert members[quill["id"]] == 0 and members[scout["id"]] == -1
            # Changing a mode later does not skip anything it has not read.
            await c.patch(f"/groups/{group['id']}/members/{scout['id']}", json={"mode": "all"})
            members = {m.agent.key.split("@")[0]: m.cursor for m in await store.channel_members(f"group/{group['id']}")}
            assert members[scout["id"]] == -1
        finally:
            await _wipe(c.tenant)


@pytest.mark.requires_postgres
async def test_changes_to_a_group_or_an_agent_make_members_rebuild_and_a_deleted_agent_leaves():
    async with session() as c:
        try:
            scout = (await c.post("/agents", json={"name": "Scout"})).json()
            quill = (await c.post("/agents", json={"name": "Quill"})).json()
            group = (await c.post("/groups", json={"name": "Trip", "members": [{"agent_id": scout["id"]}, {"agent_id": quill["id"]}]})).json()
            runtime = app.state.ctx.runtime
            forgotten: list[str] = []
            real = runtime.forget
            runtime.forget = lambda actor: forgotten.append(actor.key) or real(actor)
            try:
                await c.patch(f"/agents/{quill['id']}", json={"name": "Quillian"})
                # Scout must learn Quill's new name too, so both members are rebuilt.
                assert {k.split("@")[0] for k in forgotten} == {scout["id"], quill["id"]}
                forgotten.clear()
                await c.patch(f"/groups/{group['id']}", json={"name": "Trip 2"})
                assert len(forgotten) == 2
            finally:
                runtime.forget = real
            assert (await c.delete(f"/agents/{quill['id']}")).status_code == 204
            members = await runtime.store.channel_members(f"group/{group['id']}")
            assert [m.agent.key.split("@")[0] for m in members] == [scout["id"]]
        finally:
            await _wipe(c.tenant)


@pytest.mark.requires_postgres
async def test_a_reader_waiting_for_messages_gets_one_the_moment_it_is_sent_and_sees_who_is_working():
    async with session() as c:
        try:
            scout = (await c.post("/agents", json={"name": "Scout"})).json()
            group = (await c.post("/groups", json={"name": "Trip", "members": [{"agent_id": scout["id"], "mode": "muted"}]})).json()
            waiting = asyncio.create_task(c.get(f"/groups/{group['id']}/messages", params={"after": -1, "wait": 3}))
            await asyncio.sleep(0.4)
            started = asyncio.get_event_loop().time()
            await c.post(f"/groups/{group['id']}/messages", json={"text": "hello"})
            body = (await asyncio.wait_for(waiting, 5)).json()
            assert [e["text"] for e in body["entries"]] == ["hello"]
            assert asyncio.get_event_loop().time() - started < 1.5
            quiet = (await c.get(f"/groups/{group['id']}/messages", params={"after": 0, "wait": 0.2})).json()
            assert quiet["entries"] == [] and quiet["working"] == []
        finally:
            await _wipe(c.tenant)


@pytest.mark.requires_postgres
async def test_the_groups_token_cap_is_set_validated_and_used_is_reported():
    async with session() as c:
        try:
            scout = (await c.post("/agents", json={"name": "Scout"})).json()
            group = (await c.post("/groups", json={"name": "Trip", "members": [{"agent_id": scout["id"]}]})).json()
            assert group["token_cap"] == 1_000_000 and group["tokens_used"] == 0
            assert (await c.patch(f"/groups/{group['id']}", json={"token_cap": 5})).status_code == 422
            changed = (await c.patch(f"/groups/{group['id']}", json={"token_cap": 250_000})).json()
            assert changed["token_cap"] == 250_000
            limit = await app.state.ctx.runtime.store.accounts_exhausted([f"channel:group/{group['id']}"])
            assert limit is None
            assert (await c.get(f"/groups/{group['id']}")).json()["token_cap"] == 250_000
        finally:
            await _wipe(c.tenant)


@pytest.mark.requires_postgres
async def test_files_are_uploaded_to_the_group_listed_and_attached_to_a_message_only_if_they_are_its_own():
    async with session() as c:
        try:
            scout = (await c.post("/agents", json={"name": "Scout"})).json()
            group = (await c.post("/groups", json={"name": "Trip", "members": [{"agent_id": scout["id"], "mode": "muted"}]})).json()
            other = (await c.post("/groups", json={"name": "Other", "members": [{"agent_id": scout["id"], "mode": "muted"}]})).json()
            up = await c.post(f"/groups/{group['id']}/files", files={"file": ("../../budget.csv", b"a,b\n1,2\n", "text/csv")})
            assert up.status_code == 201, up.text
            first = up.json()
            assert first["name"] == "budget.csv" and first["size"] == 8
            # The text of the file is read now, so the agents are shown it with the message.
            assert first["excerpt"] == "a,b\n1,2" and first["truncated"] is False
            # The same name again does not replace it.
            second = (await c.post(f"/groups/{group['id']}/files", files={"file": ("budget.csv", b"x", "text/csv")})).json()
            assert second["name"] == "budget (2).csv"
            assert {f["name"] for f in (await c.get(f"/groups/{group['id']}/files")).json()} == {"budget.csv", "budget (2).csv"}
            assert (await c.get(f"/groups/{other['id']}/files")).json() == []

            sent = await c.post(f"/groups/{group['id']}/messages", json={"text": "", "attachments": [first]})
            assert sent.status_code == 201, sent.text
            assert [a["name"] for a in sent.json()["attachments"]] == ["budget.csv"]
            # What the agents get to see travels in the entry itself.
            stored = (await app.state.ctx.runtime.store.channel_read(f"group/{group['id']}"))[0]
            assert stored.data["attachments"][0]["excerpt"] == "a,b\n1,2"
            # A key from another group (or anywhere else) cannot be attached.
            stolen = await c.post(f"/groups/{other['id']}/messages", json={"text": "hi", "attachments": [{"key": first["key"], "name": "budget.csv"}]})
            assert stolen.status_code == 422
            assert (await c.post(f"/groups/{group['id']}/messages", json={"text": ""})).status_code == 422

            messages = (await c.get(f"/groups/{group['id']}/messages")).json()
            assert messages["entries"][0]["attachments"][0]["key"] == first["key"]
            # Served by the existing object route, to its owner only.
            got = await c.get("/files/object", params={"key": first["key"]})
            assert got.status_code == 200 and got.content == b"a,b\n1,2\n"
        finally:
            await _wipe(c.tenant)


@pytest.mark.requires_postgres
async def test_a_list_previews_files_when_a_message_is_only_files():
    async with session() as c:
        try:
            scout = (await c.post("/agents", json={"name": "Scout"})).json()
            group = (await c.post("/groups", json={"name": "Trip", "members": [{"agent_id": scout["id"], "mode": "muted"}]})).json()
            up = (await c.post(f"/groups/{group['id']}/files", files={"file": ("plan.pdf", b"%PDF-1", "application/pdf")})).json()
            await c.post(f"/groups/{group['id']}/messages", json={"text": "", "attachments": [up]})
            listed = (await c.get("/groups")).json()[0]
            assert listed["last_message"] == "📎 plan.pdf" and listed["working"] == []
        finally:
            await _wipe(c.tenant)


@pytest.mark.requires_postgres
async def test_a_pdf_gets_a_first_page_preview_and_a_page_count_that_stay_out_of_the_file_list():
    import io

    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument.new()
    pdf.new_page(200, 200)
    pdf.new_page(200, 200)
    raw = io.BytesIO()
    pdf.save(raw)

    async with session() as c:
        try:
            scout = (await c.post("/agents", json={"name": "Scout"})).json()
            group = (await c.post("/groups", json={"name": "Trip", "members": [{"agent_id": scout["id"], "mode": "muted"}]})).json()
            up = (await c.post(f"/groups/{group['id']}/files", files={"file": ("plan.pdf", raw.getvalue(), "application/pdf")})).json()
            assert up["pages"] == 2 and up["preview_key"].endswith(".previews/plan.pdf.png")
            png = await c.get("/files/object", params={"key": up["preview_key"]})
            assert png.status_code == 200 and png.content[:4] == b"\x89PNG"
            assert [f["name"] for f in (await c.get(f"/groups/{group['id']}/files")).json()] == ["plan.pdf"]
            sent = (await c.post(f"/groups/{group['id']}/messages", json={"text": "", "attachments": [up]})).json()
            assert sent["attachments"][0]["pages"] == 2 and sent["attachments"][0]["preview_key"] == up["preview_key"]
            # Text files have no picture; they show their first lines instead.
            txt = (await c.post(f"/groups/{group['id']}/files", files={"file": ("a.txt", b"hello", "text/plain")})).json()
            assert txt["preview_key"] is None and txt["excerpt"] == "hello"
        finally:
            await _wipe(c.tenant)
