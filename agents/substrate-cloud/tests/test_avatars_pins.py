"""A picture for an agent or a group, and the few chats pinned to the top of the user's list. Both live on the account, not in a browser."""

from __future__ import annotations

import io

import pytest
from PIL import Image
from sqlalchemy import text

from substrate_cloud.monolith.app import app

from test_scheduled_notifications import session


def _image(fmt: str = "PNG", size=(600, 300), color=(200, 30, 30)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, fmt)
    return out.getvalue()


async def _wipe(tenant: str) -> None:
    async with app.state.session_factory() as db:
        await db.execute(text("SELECT set_config('app.bypass_rls', 'on', false)"))
        for table in ("groups", "agents"):
            await db.execute(
                text(f"DELETE FROM {table} WHERE tenant_id = :t"), {"t": tenant}
            )
        await db.commit()


@pytest.mark.requires_postgres
@pytest.mark.parametrize("kind", ["agents", "groups"])
async def test_a_picture_is_cropped_square_served_to_its_owner_replaced_and_removed(
    kind,
):
    async with session() as c:
        try:
            scout = (await c.post("/agents", json={"name": "Scout"})).json()
            owner = (
                scout
                if kind == "agents"
                else (
                    await c.post(
                        "/groups",
                        json={"name": "Trip", "members": [{"agent_id": scout["id"]}]},
                    )
                ).json()
            )
            assert owner["avatar"] is None

            put = await c.put(
                f"/{kind}/{owner['id']}/avatar",
                files={"file": ("me.png", _image(), "image/png")},
            )
            assert put.status_code == 200, put.text
            first = put.json()["avatar"]
            stored = await c.get("/files/object", params={"key": first})
            assert stored.status_code == 200
            picture = Image.open(io.BytesIO(stored.content))
            assert picture.size == (
                256,
                256,
            )  # centre-cropped to a square, whatever went in
            assert first in {x["avatar"] for x in (await c.get(f"/{kind}")).json()}

            second = (
                await c.put(
                    f"/{kind}/{owner['id']}/avatar",
                    files={
                        "file": (
                            "b.jpg",
                            _image("JPEG", (50, 80), (0, 0, 200)),
                            "image/jpeg",
                        )
                    },
                )
            ).json()["avatar"]
            assert second != first
            assert (
                await c.get("/files/object", params={"key": first})
            ).status_code == 404  # the old one is not kept

            assert (await c.delete(f"/{kind}/{owner['id']}/avatar")).status_code == 200
            assert (
                await c.get("/files/object", params={"key": second})
            ).status_code == 404
            assert (await c.get(f"/{kind}/{owner['id']}")).json()["avatar"] is None
        finally:
            await _wipe(c.tenant)


@pytest.mark.requires_postgres
async def test_only_pictures_are_accepted():
    async with session() as c:
        try:
            scout = (await c.post("/agents", json={"name": "Scout"})).json()
            url = f"/agents/{scout['id']}/avatar"
            svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
            for name, data, mime in [
                ("a.svg", svg, "image/svg+xml"),
                ("a.png", b"not an image", "image/png"),
                ("a.txt", b"hello", "text/plain"),
                ("a.png", svg, "image/png"),
            ]:
                assert (
                    await c.put(url, files={"file": (name, data, mime)})
                ).status_code == 422, name
            assert (
                await c.put(
                    url,
                    files={
                        "file": (
                            "big.png",
                            _image("PNG", (4000, 4000)) + b"0" * (6 * 1024 * 1024),
                            "image/png",
                        )
                    },
                )
            ).status_code in (413, 422)
            assert (await c.get(f"/agents/{scout['id']}")).json()["avatar"] is None
        finally:
            await _wipe(c.tenant)


@pytest.mark.requires_postgres
async def test_at_most_five_chats_are_pinned_across_agents_and_groups():
    async with session() as c:
        try:
            agents = [
                (await c.post("/agents", json={"name": f"A{i}"})).json()
                for i in range(5)
            ]
            group = (
                await c.post(
                    "/groups",
                    json={"name": "Trip", "members": [{"agent_id": agents[0]["id"]}]},
                )
            ).json()
            for a in agents[:4]:
                assert (await c.put(f"/agents/{a['id']}/pin")).status_code == 200
            assert (await c.put(f"/groups/{group['id']}/pin")).json()[
                "pinned_at"
            ]  # the fifth
            assert (await c.put(f"/agents/{agents[4]['id']}/pin")).status_code == 409
            assert (
                await c.put(f"/agents/{agents[0]['id']}/pin")
            ).status_code == 200  # pinning what is pinned changes nothing

            assert (await c.delete(f"/groups/{group['id']}/pin")).json()[
                "pinned_at"
            ] is None
            assert (await c.put(f"/agents/{agents[4]['id']}/pin")).status_code == 200
            listed = {
                a["name"]: a["pinned_at"] for a in (await c.get("/agents")).json()
            }
            assert sum(1 for v in listed.values() if v) == 5
        finally:
            await _wipe(c.tenant)
