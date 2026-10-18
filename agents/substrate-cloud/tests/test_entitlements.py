"""Per-tenant entitlements: the API the platform configures and the chat limiter that enforces it."""

from __future__ import annotations

import httpx
import pytest
from _oidc import FakeRealm
from fastapi import FastAPI, HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from substrate_cloud.monolith.database import get_db
from substrate_cloud.monolith.models import TenantEntitlement
from substrate_cloud.monolith.routes.chat import enforce_message_entitlement
from substrate_cloud.monolith.routes.entitlements import router
from substrate_cloud.monolith.services.entitlements import (
    get_entitlements,
    set_entitlements,
)
from substrate_cloud.shared.auth.claims import AuthClaims
from substrate_cloud.shared.problem import install_problem_handlers

pytestmark = pytest.mark.requires_postgres


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, int] = {}

    async def incrby(self, key: str, amount: int) -> int:
        self.store[key] = self.store.get(key, 0) + amount
        return self.store[key]

    async def expire(self, key: str, seconds: int) -> None:
        pass


@pytest.fixture
async def sessions(database_url: str):
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(TenantEntitlement.__table__.create, checkfirst=True)
        await conn.execute(TenantEntitlement.__table__.delete())
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def client(sessions):
    realm = FakeRealm()
    app = FastAPI()
    install_problem_handlers(app)
    app.include_router(router, prefix="/api/v1")
    app.state.token_verifier = realm.verifier()

    async def db():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t/api/v1"
    ) as c:
        c.realm = realm  # type: ignore[attr-defined]
        yield c


async def test_no_row_means_unlimited(sessions):
    async with sessions() as db:
        found = await get_entitlements(db, "org-1")
    assert found.daily_message_limit is None and found.features == {}


async def test_set_is_idempotent_and_replaces(sessions):
    async with sessions() as db:
        await set_entitlements(
            db, "org-1", daily_message_limit=5, features={"a": 1}, updated_by="svc"
        )
        await set_entitlements(
            db, "org-1", daily_message_limit=7, features={}, updated_by="svc"
        )
        found = await get_entitlements(db, "org-1")
    assert (found.daily_message_limit, found.features) == (7, {})


async def test_the_limiter_counts_per_tenant_and_stops_at_the_limit(sessions):
    redis = FakeRedis()
    async with sessions() as db:
        await set_entitlements(
            db, "org-1", daily_message_limit=2, features={}, updated_by="svc"
        )
        alice, bob = (
            AuthClaims(sub="alice", tenant_id="org-1"),
            AuthClaims(sub="bob", tenant_id="org-1"),
        )
        await enforce_message_entitlement(db, redis, alice)
        await enforce_message_entitlement(
            db, redis, bob
        )  # the same tenant shares one counter
        with pytest.raises(HTTPException) as refused:
            await enforce_message_entitlement(db, redis, alice)
        assert (
            refused.value.status_code == 429 and "Retry-After" in refused.value.headers
        )
        # another tenant has no entitlement row: unlimited, and untouched by the first one's counter
        for _ in range(5):
            await enforce_message_entitlement(
                db, redis, AuthClaims(sub="carol", tenant_id="org-2")
            )


async def test_a_tenant_reads_its_own_entitlements_and_cannot_change_them(
    client, sessions
):
    async with sessions() as db:
        await set_entitlements(
            db, "org-1", daily_message_limit=50, features={"x": True}, updated_by="svc"
        )
    mine = await client.get("/entitlements", headers=client.realm.bearer(org="org-1"))  # type: ignore[attr-defined]
    assert mine.json() == {
        "tenant_id": "org-1",
        "daily_message_limit": 50,
        "features": {"x": True},
    }
    other = await client.get("/entitlements", headers=client.realm.bearer(org="org-2"))  # type: ignore[attr-defined]
    assert other.json()["daily_message_limit"] is None
    for roles in ([], ["tenant_admin"]):
        denied = await client.put(
            "/tenants/org-1/entitlements",
            json={"daily_message_limit": 10**6},
            headers=client.realm.bearer(org="org-1", roles=roles),  # type: ignore[attr-defined]
        )
        assert denied.status_code == 403


async def test_cell_admin_and_platform_admin_configure_entitlements(client):
    realm: FakeRealm = client.realm  # type: ignore[attr-defined]
    service = realm.bearer(
        "cell",
        org=None,
        username="service-account-cell-admin",
        client_roles=["provision-tenant"],
    )
    saved = await client.put(
        "/tenants/org-7/entitlements",
        json={"daily_message_limit": 100, "features": {"pro": True}},
        headers=service,
    )
    assert saved.status_code == 200 and saved.json()["daily_message_limit"] == 100
    admin = await client.put(
        "/tenants/org-7/entitlements",
        json={"daily_message_limit": None},
        headers=realm.bearer(roles=["platform_admin"]),
    )
    assert admin.status_code == 200 and admin.json()["daily_message_limit"] is None
    wrong_service = realm.bearer("cell", org=None, username="service-account-other")
    assert (
        await client.put("/tenants/org-7/entitlements", json={}, headers=wrong_service)
    ).status_code == 403
    bad = await client.put(
        "/tenants/org-7/entitlements", json={"daily_message_limit": -1}, headers=service
    )
    assert bad.status_code == 422 and bad.headers["content-type"].startswith(
        "application/problem+json"
    )
