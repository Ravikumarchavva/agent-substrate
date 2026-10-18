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


async def test_workspace_file_and_versions(app):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Save file via JSON body
            save_resp = await client.put(
                "/api/v1/workspace/file",
                json={
                    "path": "script.py",
                    "content": "print('hello world')",
                    "session_id": "s1",
                },
            )
            assert save_resp.status_code == 200

            # Get file
            get_resp = await client.get("/api/v1/workspace/file?path=script.py&session_id=s1")
            assert get_resp.status_code == 200
            assert get_resp.content == b"print('hello world')"

            # List files
            list_resp = await client.get("/api/v1/workspace/files?session_id=s1")
            assert list_resp.status_code == 200
            assert len(list_resp.json()["files"]) >= 1

            # Get versions
            ver_resp = await client.get("/api/v1/workspace/versions?path=script.py&session_id=s1")
            assert ver_resp.status_code == 200
            versions = ver_resp.json()["versions"]
            assert len(versions) >= 1
            seq = versions[0]["seq"]

            # Restore version
            rest_resp = await client.post(
                "/api/v1/workspace/versions/restore",
                json={"path": "script.py", "seq": seq, "session_id": "s1"},
            )
            assert rest_resp.status_code == 200

            # Usage
            usage_resp = await client.get("/api/v1/workspace/usage")
            assert usage_resp.status_code == 200
            assert usage_resp.json()["used_bytes"] > 0

            # Delete file
            del_resp = await client.delete("/api/v1/workspace/files?path=script.py&session_id=s1")
            assert del_resp.status_code == 200


async def test_artifacts_crud_and_promotion(app):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Create session artifact
            create_resp = await client.post(
                "/api/v1/artifacts?scope=session&thread_id=th-1",
                json={
                    "title": "Quarterly Report",
                    "type": "markdown",
                    "body": "# Q3 Results\nRevenue is up.",
                },
            )
            assert create_resp.status_code == 200
            art = create_resp.json()
            assert art["slug"] == "quarterly-report"

            # Get artifact
            get_resp = await client.get(
                "/api/v1/artifacts/quarterly-report?scope=session&thread_id=th-1"
            )
            assert get_resp.status_code == 200
            assert get_resp.json()["title"] == "Quarterly Report"

            # Promote artifact
            prom_resp = await client.post(
                "/api/v1/artifacts/quarterly-report/promote?thread_id=th-1"
            )
            assert prom_resp.status_code == 200

            # List global artifacts
            list_resp = await client.get("/api/v1/artifacts")
            assert list_resp.status_code == 200
            assert len(list_resp.json()["items"]) >= 1
