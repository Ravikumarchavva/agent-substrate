"""What a group member can see of what is shared, and share of what it made: pictures reach it downscaled, and a file at a path in its sandbox
becomes an attachment of the group."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from substrate.stores import Store
from substrate.workspace.layout import conversation_shared_key
from substrate_cloud.monolith.services.groups.drives import Drive
from substrate_cloud.monolith.services.groups.media import (
    MAX_SIDE,
    Sandbox,
    load_media,
    publish,
)

TENANT, USER = "t1", "u1"
HOME = "dot-6f1c0a2e-0000-4000-8000-000000000001"
TRIP = "group-6f1c0a2e-0000-4000-8000-000000000002"
KITCHEN = "group-6f1c0a2e-0000-4000-8000-000000000003"
SANDBOX = Sandbox(
    tenant_id=TENANT,
    user_id=USER,
    home=HOME,
    group=TRIP,
    drives=(Drive("trip", "Trip", TRIP), Drive("kitchen", "Kitchen", KITCHEN)),
)


def _png(size=(3000, 2000)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (10, 120, 200)).save(out, "PNG")
    return out.getvalue()


def _key(workspace: str, rel: str) -> str:
    return conversation_shared_key(TENANT, USER, workspace, rel)


@pytest.fixture
def files(tmp_path):
    return Store.at(tmp_path / "objects", file_quota_bytes=50_000_000).files


async def test_a_large_picture_reaches_the_model_no_larger_than_it_needs(files):
    await files.upload(_key(TRIP, "uploads/big.png"), _png())
    block = await load_media(
        files,
        SANDBOX,
        {"name": "big.png", "mime": "image/png", "key": _key(TRIP, "uploads/big.png")},
    )
    assert block is not None and block.type == "image" and block.filename == "big.png"
    assert max(Image.open(io.BytesIO(block.data)).size) <= MAX_SIDE


async def test_only_the_groups_own_pictures_can_be_opened(files):
    await files.upload(_key(KITCHEN, "uploads/other.png"), _png((10, 10)))
    other = {
        "name": "other.png",
        "mime": "image/png",
        "key": _key(KITCHEN, "uploads/other.png"),
    }
    assert (
        await load_media(files, SANDBOX, other) is None
    )  # another group's file, even one this agent shares
    assert (
        await load_media(
            files,
            SANDBOX,
            {
                "name": "x.png",
                "mime": "image/png",
                "key": _key(TRIP, "uploads/missing.png"),
            },
        )
        is None
    )
    await files.upload(_key(TRIP, "uploads/notes.png"), b"not a picture")
    assert (
        await load_media(
            files,
            SANDBOX,
            {
                "name": "notes.png",
                "mime": "image/png",
                "key": _key(TRIP, "uploads/notes.png"),
            },
        )
        is None
    )


async def test_a_file_in_the_groups_folder_is_shared_where_it_is(files):
    await files.upload(_key(TRIP, "report.csv"), b"a,b\n1,2\n")
    record = await publish(files, None, SANDBOX, "/groups/trip/report.csv")
    assert record["name"] == "report.csv" and record["key"] == _key(TRIP, "report.csv")
    assert record["mime"] == "text/csv" and "1,2" in record["excerpt"]


async def test_a_file_in_the_agents_own_folder_is_copied_into_the_group_to_share_it(
    files,
):
    await files.upload(_key(HOME, "out/chart.png"), b"PNGDATA")
    record = await publish(files, None, SANDBOX, "/workspace/out/chart.png")
    assert record["key"] == _key(TRIP, "shared/chart.png")
    assert await files.download(record["key"]) == b"PNGDATA"
    assert await files.exists(_key(HOME, "out/chart.png"))  # it is a copy
    again = await publish(files, None, SANDBOX, "/workspace/out/chart.png")
    assert again["key"] == _key(
        TRIP, "shared/chart (2).png"
    )  # never replacing what is there


async def test_a_file_from_another_group_it_is_in_is_copied_too(files):
    await files.upload(_key(KITCHEN, "menu.txt"), b"soup")
    record = await publish(files, None, SANDBOX, "/groups/kitchen/menu.txt")
    assert record["key"] == _key(TRIP, "shared/menu.txt")


async def test_a_path_that_is_not_a_file_it_may_share_is_refused_with_the_reason(files):
    await files.upload(_key(HOME, "secret.txt"), b"s")
    for path, why in [
        ("/workspace/nothing.txt", "no such file"),
        ("/groups/unknown/x.txt", "no such folder"),
        ("/etc/passwd", "under /workspace or /groups"),
        ("/workspace/../groups/trip/x", "under /workspace or /groups"),
        ("/workspace/", "no such file"),
        ("relative.txt", "under /workspace or /groups"),
    ]:
        with pytest.raises(ValueError, match=why):
            await publish(files, None, SANDBOX, path)


# -- a recording shared in a group is understood: what it says travels with it ------------------------------------------------------------


@pytest.mark.requires_postgres
async def test_a_shared_recording_carries_what_it_says_and_one_that_cannot_be_transcribed_is_still_shared(
    monkeypatch,
):
    from sqlalchemy import text

    from substrate_cloud.monolith.app import app
    from substrate_cloud.monolith.services import transcription

    from test_scheduled_notifications import session

    async def hears(app_state, raw, filename, **_):
        if raw == b"broken":
            raise transcription.TranscriptionUnavailable("no speech model")
        return "meet at noon"

    monkeypatch.setattr(transcription, "transcribe", hears)
    async with session() as c:
        try:
            scout = (await c.post("/agents", json={"name": "Scout"})).json()
            group = (
                await c.post(
                    "/groups",
                    json={
                        "name": "Trip",
                        "members": [{"agent_id": scout["id"], "mode": "muted"}],
                    },
                )
            ).json()
            note = (
                await c.post(
                    f"/groups/{group['id']}/files",
                    files={"file": ("note.webm", b"audio-bytes", "audio/webm")},
                )
            ).json()
            assert note["transcript"] == "meet at noon"
            broken = (
                await c.post(
                    f"/groups/{group['id']}/files",
                    files={"file": ("bad.webm", b"broken", "audio/webm")},
                )
            ).json()
            assert broken["transcript"] is None  # shared all the same

            sent = (
                await c.post(
                    f"/groups/{group['id']}/messages",
                    json={"text": "", "attachments": [note, broken]},
                )
            ).json()
            assert [a["transcript"] for a in sent["attachments"]] == [
                "meet at noon",
                None,
            ]
        finally:
            async with app.state.session_factory() as db:
                await db.execute(
                    text("SELECT set_config('app.bypass_rls', 'on', false)")
                )
                for table in ("groups", "agents"):
                    await db.execute(
                        text(f"DELETE FROM {table} WHERE tenant_id = :t"),
                        {"t": c.tenant},
                    )
                await db.commit()
