import httpx
import pytest
from pydantic import ValidationError

from loamz.agents.bootstrap import Container, Settings
from loamz.agents.bootstrap.http import caller as caller_dependency
from loamz.agents.features.people.preferences.model import Preferences
from loamz.agents.features.people.preferences.service import PreferencesService
from loamz.agents.features.people.preferences.store import PreferencesStore
from loamz.agents.kernel.caller import Caller
from loamz.agents.server import create_app

ALICE = Caller(tenant_id="acme", user_id="alice")
ALICE_ELSEWHERE = Caller(tenant_id="globex", user_id="alice")
BOB = Caller(tenant_id="acme", user_id="bob")


async def test_preferences_belong_to_one_person_in_one_tenant(db):
    service = PreferencesService(PreferencesStore(db))
    assert await service.get(ALICE) == Preferences()
    await service.replace(ALICE, Preferences(display_name="  Al  ice ", timezone="Europe/London"))
    assert (await service.get(ALICE)).display_name == "Al ice"
    assert await service.get(BOB) == Preferences()
    assert await service.get(ALICE_ELSEWHERE) == Preferences()
    assert "Europe/London" in await service.instructions_for(ALICE)
    assert await service.erase("acme") == 1
    assert await service.get(ALICE) == Preferences()


def test_rules_are_enforced_by_the_model():
    with pytest.raises(ValidationError):
        Preferences(timezone="Mars/Olympus")
    with pytest.raises(ValidationError):
        Preferences(models={"x": "y" * 5000})


async def test_the_api_reads_and_replaces_the_callers_preferences(tmp_path):
    container = Container(Settings(database={"path": str(tmp_path / "agents.db")}))
    app = create_app(container)
    app.dependency_overrides[caller_dependency] = lambda: ALICE
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as client:
            assert (await client.get("/api/v1/me/preferences")).json()["timezone"] == ""
            saved = await client.put("/api/v1/me/preferences", json={"timezone": "Asia/Kolkata"})
            assert saved.status_code == 200 and saved.json()["timezone"] == "Asia/Kolkata"
            bad = await client.put("/api/v1/me/preferences", json={"timezone": "nowhere"})
            assert bad.status_code == 422
            assert bad.headers["content-type"].startswith("application/problem+json")


async def test_without_sign_in_configured_every_feature_route_is_closed(tmp_path):
    app = create_app(Container(Settings(database={"path": str(tmp_path / "a.db")})))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as client:
            response = await client.get("/api/v1/me/preferences")
    assert response.status_code == 401 and response.json()["type"].endswith("/unauthenticated")
