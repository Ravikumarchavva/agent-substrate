import httpx
import pytest

from loamz.agents.bootstrap import Container, Settings
from loamz.agents.bootstrap.http import caller as caller_dependency
from loamz.agents.kernel.caller import Caller
from loamz.agents.server import create_app

ALICE = Caller(tenant_id="acme", user_id="alice")


@pytest.fixture
def app(tmp_path):
    container = Container(Settings(database={"path": str(tmp_path / "agents.db")}))
    application = create_app(container, roles={"all"})
    application.dependency_overrides[caller_dependency] = lambda: ALICE
    return application


async def test_groups_and_drives_crud(app):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            # 1. Create group
            resp = await client.post("/api/v1/groups", json={"name": "Engineering"})
            assert resp.status_code == 200
            group = resp.json()
            group_id = group["id"]

            # 2. List groups
            list_resp = await client.get("/api/v1/groups")
            assert list_resp.status_code == 200
            assert len(list_resp.json()["items"]) == 1

            # 3. Add member
            m_resp = await client.post(
                f"/api/v1/groups/{group_id}/members",
                json={"agent_id": "agent-1", "mode": "always_on"},
            )
            assert m_resp.status_code == 200

            # 4. Send message
            msg_resp = await client.post(
                f"/api/v1/groups/{group_id}/messages",
                json={"text": "Hello team!"},
            )
            assert msg_resp.status_code == 200
            msg = msg_resp.json()
            seq = msg["seq"]

            # 5. React to message
            r_resp = await client.put(
                f"/api/v1/groups/{group_id}/messages/{seq}/reaction",
                json={"reaction": "👍"},
            )
            assert r_resp.status_code == 204

            # 6. Read messages
            msgs_resp = await client.get(f"/api/v1/groups/{group_id}/messages")
            assert msgs_resp.status_code == 200
            assert len(msgs_resp.json()["items"]) == 1

            # 7. Pin group
            pin_resp = await client.put(f"/api/v1/groups/{group_id}/pin")
            assert pin_resp.status_code == 200

            unpin_resp = await client.delete(f"/api/v1/groups/{group_id}/pin")
            assert unpin_resp.status_code == 200

            # 8. Drives: upload file to group
            files = {"file": ("notes.txt", b"meeting notes content", "text/plain")}
            up_resp = await client.post(f"/api/v1/groups/{group_id}/files", files=files)
            assert up_resp.status_code == 200
            assert up_resp.json()["name"] == "notes.txt"

            # 9. List files in group drive
            dfiles_resp = await client.get(f"/api/v1/groups/{group_id}/files")
            assert dfiles_resp.status_code == 200
            assert len(dfiles_resp.json()["files"]) == 1

            # 10. Delete group
            del_resp = await client.delete(f"/api/v1/groups/{group_id}")
            assert del_resp.status_code == 204


async def test_feed_endpoint(app):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/v1/feed", json={"since": {}})
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]
