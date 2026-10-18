"""Keycloak token verification in the platform: tenant, role and scope mapping, and RFC 9457 errors."""

from __future__ import annotations

import httpx
import pytest
from _oidc import FakeRealm
from fastapi import Depends, FastAPI

from substrate_cloud.shared.auth import (
    AuthClaims,
    get_current_user,
    optional_current_user,
    require_role,
    require_scopes,
    require_service_identity,
)
from substrate_cloud.shared.problem import Problem, install_problem_handlers


@pytest.fixture
def realm() -> FakeRealm:
    return FakeRealm()


@pytest.fixture
def app(realm) -> FastAPI:
    app = FastAPI()
    install_problem_handlers(app)
    app.state.token_verifier = realm.verifier()

    @app.get("/me")
    async def me(user: AuthClaims = Depends(get_current_user)):
        return user.model_dump(mode="json")

    @app.get("/maybe")
    async def maybe(user: AuthClaims | None = Depends(optional_current_user)):
        return {"sub": user.sub if user else None}

    @app.get(
        "/admin", dependencies=[Depends(require_role("platform_admin", "tenant_admin"))]
    )
    async def admin():
        return {}

    @app.get("/files", dependencies=[Depends(require_scopes("files:read"))])
    async def files():
        return {}

    @app.post(
        "/erase", dependencies=[Depends(require_service_identity("erase-tenant"))]
    )
    async def erase():
        return {}

    @app.get("/boom")
    async def boom():
        raise Problem(409, "version-conflict", "Stale write", current_version=4)

    return app


async def get(app, path, headers=None, method="GET", **kwargs):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as c:
        return await c.request(method, path, headers=headers, **kwargs)


async def test_the_tenant_is_the_organization_id_of_the_token(app, realm):
    body = (
        await get(app, "/me", realm.bearer("u1", org="org-42", email="a@b.test"))
    ).json()
    assert (
        body["tenant_id"] == "org-42"
        and body["sub"] == "u1"
        and body["email"] == "a@b.test"
    )
    assert (
        body["role"] == "end_user" and body["kind"] == "user" and body["scopes"] is None
    )


@pytest.mark.parametrize(
    ("roles", "expected"),
    [
        ([], "end_user"),
        (["offline_access", "uma_authorization"], "end_user"),
        (["tenant_admin"], "tenant_admin"),
        (["tenant_admin", "platform_admin"], "platform_admin"),
    ],
)
async def test_realm_roles_map_to_the_platform_role(app, realm, roles, expected):
    assert (await get(app, "/me", realm.bearer(roles=roles))).json()["role"] == expected


async def test_scopes_of_a_delegated_token_are_carried(app, realm):
    body = (
        await get(app, "/me", realm.bearer(scope="openid files:read data:read"))
    ).json()
    assert sorted(body["scopes"]) == ["data:read", "files:read"]


async def test_one_organization_per_token(app, realm):
    two = realm.token(
        org=None, organization={"acme": {"id": "a"}, "globex": {"id": "b"}}
    )
    response = await get(app, "/me", {"Authorization": f"Bearer {two}"})
    assert response.status_code == 403 and response.json()["type"].endswith(
        "ambiguous-organization"
    )


async def test_role_guard(app, realm):
    assert (
        await get(app, "/admin", realm.bearer(roles=["tenant_admin"]))
    ).status_code == 200
    denied = await get(app, "/admin", realm.bearer(roles=[]))
    assert denied.status_code == 403 and denied.json()["type"].endswith("forbidden")


async def test_scope_guard(app, realm):
    assert (
        await get(app, "/files", realm.bearer())
    ).status_code == 200  # an ordinary user token
    assert (
        await get(app, "/files", realm.bearer(scope="openid files:read"))
    ).status_code == 200
    narrow = await get(app, "/files", realm.bearer(scope="openid data:read"))
    assert narrow.status_code == 403 and narrow.json()["type"].endswith(
        "insufficient-scope"
    )


async def test_service_accounts_need_the_named_role(app, realm):
    service = dict(username="service-account-cell-admin", org=None)
    ok = await get(
        app,
        "/erase",
        realm.bearer("svc", client_roles=["erase-tenant"], **service),
        method="POST",
    )
    assert ok.status_code == 200
    missing = await get(app, "/erase", realm.bearer("svc", **service), method="POST")
    assert missing.status_code == 403
    user = await get(
        app, "/erase", realm.bearer("u", client_roles=["erase-tenant"]), method="POST"
    )
    assert (
        user.status_code == 403
    )  # a person is not a service account, whatever roles they hold


async def test_an_ordinary_user_without_an_organization_is_refused_but_a_service_account_is_not(
    app, realm
):
    assert (await get(app, "/me", realm.bearer(org=None))).status_code == 403
    service = await get(
        app, "/me", realm.bearer("svc", org=None, username="service-account-x")
    )
    assert service.status_code == 200 and service.json()["kind"] == "service"


async def test_optional_auth_treats_a_bad_token_as_anonymous(app, realm):
    assert (await get(app, "/maybe")).json() == {"sub": None}
    assert (
        await get(app, "/maybe", {"Authorization": f"Bearer {realm.hs256()}"})
    ).json() == {"sub": None}
    assert (await get(app, "/maybe", realm.bearer("u9"))).json() == {"sub": "u9"}


async def test_errors_are_problem_json(app, realm):
    unauthorized = await get(app, "/me")
    assert unauthorized.status_code == 401
    assert unauthorized.headers["content-type"].startswith("application/problem+json")
    assert unauthorized.headers["www-authenticate"].startswith("Bearer")
    assert unauthorized.json() == {
        "type": "https://substrate.dev/problems/unauthorized",
        "title": "Unauthorized",
        "status": 401,
        "detail": "A bearer access token is required",
        "instance": "/me",
    }
    boom = await get(app, "/boom")
    assert boom.status_code == 409 and boom.json()["current_version"] == 4
    missing = await get(app, "/nowhere")
    assert missing.status_code == 404 and missing.headers["content-type"].startswith(
        "application/problem+json"
    )


async def test_validation_errors_are_problem_json(realm):
    app = FastAPI()
    install_problem_handlers(app)

    @app.get("/n")
    async def n(count: int):
        return count

    response = await get(app, "/n?count=x")
    assert response.status_code == 422 and response.headers["content-type"].startswith(
        "application/problem+json"
    )
    assert (
        response.json()["type"].endswith("invalid-request")
        and response.json()["errors"]
    )
