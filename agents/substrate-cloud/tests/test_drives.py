"""An agent's file system: its own folder at /workspace, and each group it belongs to as a shared folder at /groups/<name>. The same folder for every
member, so what one saves another can open."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from substrate.integrations.tools.code_interpreter.code_interpreter.runtimes.base import valid_mount_label
from substrate.integrations.tools.code_interpreter.code_interpreter.tool import MAX_MOUNTS
from substrate_cloud.monolith.app import app
from substrate_cloud.monolith.services.groups.drives import MAX_DRIVES, Drive, drive_label, drives_of, files_instructions, run_metadata
from substrate_cloud.monolith.services.groups.member import build_member
from substrate_cloud.monolith.services.groups.service import member_actor

from test_scheduled_notifications import session


def test_a_groups_folder_is_its_name_made_safe_and_always_something_the_sandbox_accepts():
    assert drive_label("Trip planning!") == "trip-planning"
    assert drive_label("  Café  Crème ") == "cafe-creme"
    for odd in ["", "   ", "🚀", "../../etc", "private", "a" * 300, "-.-"]:
        assert valid_mount_label(drive_label(odd)), odd
    assert len(drive_label("a" * 300)) <= 40


def test_the_platform_never_asks_to_mount_more_than_the_code_tool_allows():
    assert MAX_DRIVES <= MAX_MOUNTS


def test_the_model_is_told_where_its_files_are():
    alone = files_instructions(())
    assert "/workspace" in alone and "/groups" not in alone
    told = files_instructions((Drive("trip", "Trip planning", "group-1"), Drive("kitchen", "Kitchen", "group-2")))
    assert "/workspace" in told and "/groups/trip" in told and "Trip planning" in told and "/groups/kitchen" in told and "uploads" in told


def test_a_run_gets_its_home_and_the_groups_as_its_workspaces():
    assert run_metadata("dot-1", ()) == {"workspace_id": "dot-1"}
    assert run_metadata("dot-1", (Drive("trip", "Trip", "group-2"),)) == {"workspace_id": "dot-1", "workspace_mounts": {"trip": "group-2"}}


async def _wipe(tenant: str) -> None:
    async with app.state.session_factory() as db:
        await db.execute(text("SELECT set_config('app.bypass_rls', 'on', false)"))
        for table in ("groups", "agents"):
            await db.execute(text(f"DELETE FROM {table} WHERE tenant_id = :t"), {"t": tenant})
        await db.commit()


@pytest.mark.requires_postgres
async def test_an_agents_drives_are_its_groups_in_the_order_they_were_made_with_equal_names_told_apart():
    async with session() as c:
        try:
            scout = (await c.post("/agents", json={"name": "Scout"})).json()
            other = (await c.post("/agents", json={"name": "Other"})).json()
            first = (await c.post("/groups", json={"name": "Trip", "members": [{"agent_id": scout["id"]}]})).json()
            second = (await c.post("/groups", json={"name": "Trip", "members": [{"agent_id": scout["id"]}]})).json()
            (await c.post("/groups", json={"name": "Not scouts", "members": [{"agent_id": other["id"]}]})).json()

            async with app.state.session_factory() as db:
                await db.execute(text("SELECT set_config('app.bypass_rls', 'on', false)"))
                drives = await drives_of(db, uuid.UUID(scout["id"]))
            assert [(d.label, d.group, d.workspace_id) for d in drives] == [
                ("trip", "Trip", f"group-{first['id']}"),
                ("trip-2", "Trip", f"group-{second['id']}"),
            ]
        finally:
            await _wipe(c.tenant)


@pytest.mark.requires_postgres
async def test_a_member_runs_in_its_own_home_with_every_group_it_is_in_mounted():
    """Not in the group's folder as its home: each member keeps its own files, and the group's are shared beside them."""
    async with session() as c:
        try:
            scout = (await c.post("/agents", json={"name": "Scout"})).json()
            quill = (await c.post("/agents", json={"name": "Quill"})).json()
            trip = (await c.post("/groups", json={"name": "Trip", "members": [{"agent_id": scout["id"]}, {"agent_id": quill["id"]}]})).json()
            kitchen = (await c.post("/groups", json={"name": "Kitchen", "members": [{"agent_id": scout["id"]}]})).json()

            built = await build_member(app.state.ctx, member_actor(scout["id"], trip["id"]))
            scope = built._config.scope
            assert scope["workspace_id"] == f"dot-{scout['id']}"
            assert scope["workspace_mounts"] == {"trip": f"group-{trip['id']}", "kitchen": f"group-{kitchen['id']}"}
            quills = (await build_member(app.state.ctx, member_actor(quill["id"], trip["id"])))._config.scope
            assert quills["workspace_id"] == f"dot-{quill['id']}" and quills["workspace_mounts"] == {"trip": f"group-{trip['id']}"}
        finally:
            await _wipe(c.tenant)


@pytest.mark.requires_postgres
async def test_deleting_a_group_deletes_its_files_too():
    async with session() as c:
        try:
            scout = (await c.post("/agents", json={"name": "Scout"})).json()
            group = (await c.post("/groups", json={"name": "Trip", "members": [{"agent_id": scout["id"]}]})).json()
            up = await c.post(f"/groups/{group['id']}/files", files={"file": ("brief.txt", b"hello", "text/plain")})
            assert up.status_code == 201
            store = app.state.ctx.files_for(c.tenant)
            prefix = up.json()["key"].removesuffix("uploads/brief.txt")
            assert await store.list_prefix(prefix)

            assert (await c.delete(f"/groups/{group['id']}")).status_code == 204

            assert await store.list_prefix(prefix) == []
        finally:
            await _wipe(c.tenant)


@pytest.mark.requires_postgres
async def test_a_change_to_who_is_in_which_group_rebuilds_the_agents_it_touches_in_every_group_they_are_in():
    """A member opens every group it is in as a folder, so what it was built with goes stale when any of them changes."""
    async with session() as c:
        try:
            scout = (await c.post("/agents", json={"name": "Scout"})).json()
            quill = (await c.post("/agents", json={"name": "Quill"})).json()
            member = lambda agent, group: f"{agent['id']}@{group['id']}"  # noqa: E731
            runtime = app.state.ctx.runtime
            forgotten: set[str] = set()
            real = runtime.forget
            runtime.forget = lambda actor: forgotten.add(actor.key) or real(actor)
            try:
                g1 = (await c.post("/groups", json={"name": "One", "members": [{"agent_id": scout["id"]}]})).json()
                g2 = (await c.post("/groups", json={"name": "Two", "members": [{"agent_id": scout["id"]}, {"agent_id": quill["id"]}]})).json()
                forgotten.clear()

                g3 = (await c.post("/groups", json={"name": "Three", "members": [{"agent_id": scout["id"]}]})).json()
                assert {member(scout, g1), member(scout, g2), member(scout, g3)} <= forgotten  # Scout has a third folder now
                forgotten.clear()

                assert (await c.delete(f"/groups/{g2['id']}/members/{scout['id']}")).status_code == 204
                assert {member(scout, g1), member(scout, g3), member(quill, g2)} <= forgotten  # Scout lost one; Quill's group changed
                forgotten.clear()

                assert (await c.delete(f"/groups/{g3['id']}")).status_code == 204
                assert {member(scout, g1), member(scout, g3)} <= forgotten  # the other groups lose a folder; the deleted one's member goes
            finally:
                runtime.forget = real
        finally:
            await _wipe(c.tenant)


@pytest.mark.requires_postgres
async def test_a_groups_drive_is_listed_under_its_name_and_a_file_written_there_is_one_of_the_groups_files():
    async with session() as c:
        try:
            scout = (await c.post("/agents", json={"name": "Scout"})).json()
            group = (await c.post("/groups", json={"name": "Trip", "members": [{"agent_id": scout["id"]}]})).json()
            drive = f"group-{group['id']}"
            up = await c.post(f"/groups/{group['id']}/files", files={"file": ("brief.txt", b"hello", "text/plain")})
            assert up.status_code == 201

            listed = [f for f in (await c.get("/workspace/files")).json()["files"] if f["session_id"] == drive]
            assert [(f["name"], f["session_name"]) for f in listed] == [("brief.txt", "Trip")]

            # the same folder from the Storage side: what is written there is among the group's own files
            put = await c.put("/workspace/file", params={"thread_id": drive, "path": "notes/plan.md"}, content=b"# plan")
            assert put.status_code == 200, put.text
            assert (await c.get("/workspace/file", params={"thread_id": drive, "path": "uploads/brief.txt"})).content == b"hello"
            assert {f["name"] for f in (await c.get(f"/groups/{group['id']}/files")).json()} == {"brief.txt", "plan.md"}

            # and nobody else's
            assert (await c.get("/workspace/file", params={"thread_id": f"group-{uuid.uuid4()}", "path": "x"})).status_code == 404
            assert (await c.get("/workspace/file", params={"thread_id": "group-not-an-id", "path": "x"})).status_code == 404
        finally:
            await _wipe(c.tenant)


@pytest.mark.requires_postgres
async def test_a_members_code_reads_what_the_user_shared_and_what_it_makes_lands_in_the_files():
    """The whole path: a group message wakes a member, whose code sees the group's folder beside its own, and what it saves is in the Files lists."""
    import asyncio

    from substrate.testing.scripted import ScriptedModel, ToolCall

    code = (
        "import os\n"
        "text = open('/groups/trip/uploads/brief.txt').read()\n"
        "open('/groups/trip/summary.txt', 'w').write(text.upper())\n"
        "open('/workspace/mine.txt', 'w').write('private')\n"
        "print(sorted(os.listdir('/groups')))\n"
    )
    async with session() as c:
        if not any(getattr(t, "name", "") == "code_interpreter" for t in app.state.ctx.tools.all()):
            pytest.skip("no sandbox on this host")
        try:
            model = ScriptedModel(ToolCall("code_interpreter", {"code": code}), "Saved the summary.")
            app.state.ctx.model_client = model
            scout = (await c.post("/agents", json={"name": "Scout"})).json()
            group = (await c.post("/groups", json={"name": "Trip", "members": [{"agent_id": scout["id"]}]})).json()
            up = await c.post(f"/groups/{group['id']}/files", files={"file": ("brief.txt", b"hello", "text/plain")})
            assert up.status_code == 201

            await c.post(f"/groups/{group['id']}/messages", json={"text": "@Scout summarise the brief."})
            for _ in range(80):
                await asyncio.sleep(0.5)
                if len((await c.get(f"/groups/{group['id']}/messages")).json()["entries"]) >= 2:
                    break
            entries = (await c.get(f"/groups/{group['id']}/messages")).json()["entries"]
            assert len(entries) == 2 and entries[1]["text"] == "Saved the summary.", entries

            drive, home = f"group-{group['id']}", f"dot-{scout['id']}"
            shown = [str(block) for message in model.seen[-1] for block in message.content]
            got = await c.get("/workspace/file", params={"thread_id": drive, "path": "summary.txt"})
            assert got.content == b"HELLO", shown[-3:]
            assert {f["name"] for f in (await c.get(f"/groups/{group['id']}/files")).json()} == {"brief.txt", "summary.txt"}
            assert (await c.get("/workspace/file", params={"thread_id": home, "path": "mine.txt"})).content == b"private"
            assert (await c.get("/workspace/file", params={"thread_id": drive, "path": "mine.txt"})).status_code == 404  # the agent's own stays its own
        finally:
            await _wipe(c.tenant)


@pytest.mark.requires_postgres
async def test_a_member_sees_a_shared_picture_and_attaches_what_its_code_made():
    import asyncio
    import io

    from PIL import Image

    from substrate.models import Modality, ModelCapabilities
    from substrate.testing.scripted import ScriptedModel, ToolCall
    from substrate.types import MediaBlock

    async with session() as c:
        if not any(getattr(t, "name", "") == "code_interpreter" for t in app.state.ctx.tools.all()):
            pytest.skip("no sandbox on this host")
        try:
            model = ScriptedModel(
                ToolCall("code_interpreter", {"code": "open('/groups/trip/caption.txt', 'w').write('a blue square')"}),
                ToolCall("attach", {"path": "/groups/trip/caption.txt"}),
                "Here is the caption.",
            )
            model.capabilities = ModelCapabilities(model_id="sees", input_modalities=frozenset({Modality.TEXT, Modality.IMAGE}))
            app.state.ctx.model_client = model
            scout = (await c.post("/agents", json={"name": "Scout"})).json()
            group = (await c.post("/groups", json={"name": "Trip", "members": [{"agent_id": scout["id"]}]})).json()
            png = io.BytesIO()
            Image.new("RGB", (40, 40), (0, 0, 255)).save(png, "PNG")
            up = (await c.post(f"/groups/{group['id']}/files", files={"file": ("square.png", png.getvalue(), "image/png")})).json()

            await c.post(f"/groups/{group['id']}/messages", json={"text": "@Scout caption this", "attachments": [up]})
            for _ in range(80):
                await asyncio.sleep(0.5)
                entries = (await c.get(f"/groups/{group['id']}/messages")).json()["entries"]
                if len(entries) >= 2:
                    break
            reply = entries[-1]
            assert reply["text"] == "Here is the caption." and [a["name"] for a in reply["attachments"]] == ["caption.txt"]
            assert any(isinstance(b, MediaBlock) and b.filename == "square.png" for m in model.seen[0] for b in m.content)
            assert "a blue square" in (await c.get("/files/object", params={"key": reply["attachments"][0]["key"]})).text
        finally:
            await _wipe(c.tenant)
