import httpx
import pytest

from loamz.engine.agents.kernel.caller import Caller
from loamz.server.agents.bootstrap import Container, Settings
from loamz.server.agents.bootstrap.http import caller as caller_dependency
from loamz.server.agents.http import create_app

ALICE = Caller(tenant_id="acme", user_id="alice")


@pytest.fixture
def app(tmp_path):
    container = Container(Settings(database={"path": str(tmp_path / "agents.db")}))
    application = create_app(container, roles={"all"})
    application.dependency_overrides[caller_dependency] = lambda: ALICE
    return application


async def test_gdpr_erasure(app):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            user_resp = await client.post(
                "/api/v1/internal/gdpr/erase-user",
                json={"tenant_id": "acme", "user_id": "alice"},
            )
            assert user_resp.status_code == 200
            assert user_resp.json()["status"] == "completed"

            tenant_resp = await client.post(
                "/api/v1/internal/gdpr/erase-tenant",
                json={"tenant_id": "acme"},
            )
            assert tenant_resp.status_code == 200
            assert tenant_resp.json()["status"] == "completed"
