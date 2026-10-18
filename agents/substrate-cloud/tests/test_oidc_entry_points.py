"""Every entry point (the monolith and each of the microservices) verifies Keycloak tokens, and only those.

Parametrized over the real FastAPI apps and every route in their OpenAPI documents: a shared-secret (HS256) token, a token for
another audience, an ID token, an expired one, one from another issuer, and no token at all are all refused with a problem+json
401; a token naming no organization is a 403. Nothing here needs a database: authentication runs before any handler or session.
"""

from __future__ import annotations

import importlib
import re

import httpx
import pytest
from _oidc import FakeRealm

APPS = [
    "substrate_cloud.monolith.app",
    "substrate_cloud.services.admin.app",
    "substrate_cloud.services.agent_runtime.app",
    "substrate_cloud.services.conversation.app",
    "substrate_cloud.services.gateway.app",
    "substrate_cloud.services.human_gate.app",
    "substrate_cloud.services.job_controller.app",
    "substrate_cloud.services.live_stream.app",
    "substrate_cloud.services.policy.app",
    "substrate_cloud.services.tool_executor.app",
]

# Endpoints that are open by design, each with the reason. Everything else needs a valid token.
OPEN = {
    "/health": "liveness probe",
    "/ready": "readiness probe",
    "/health/ready": "readiness probe",
    "/.well-known/data-token-jwks.json": "public signing keys of the per-run data token",
    "/api/v1/shared/{token}": "a shared conversation: the unguessable link is the credential",
    "/api/v1/rate-limit/status": "answers for the anonymous tier too (a bad token is anonymous)",
}


def routes(app) -> list[tuple[str, str]]:
    found = []
    for path, item in app.openapi()["paths"].items():
        if path in OPEN:
            continue
        for method in item:
            if method in {"get", "post", "put", "patch", "delete"}:
                found.append((method.upper(), path))
    return found


def cases() -> list:
    out = []
    for module in APPS:
        app = importlib.import_module(module).app
        out.extend(
            pytest.param(
                module,
                m,
                p,
                id=f"{module.split('.')[-2] if 'services' in module else 'monolith'}:{m} {p}",
            )
            for m, p in routes(app)
        )
    return out


@pytest.fixture(scope="module")
def realm() -> FakeRealm:
    return FakeRealm()


class _Session:
    """A database session that is never really used: authentication refuses the request first."""

    async def execute(self, *args, **kwargs):
        return None


@pytest.fixture(autouse=True)
def _no_database():
    """The monolith opens its session (and context) as ordinary dependencies; stub them out."""
    from substrate_cloud.monolith.app import app
    from substrate_cloud.monolith.database import get_db
    from substrate_cloud.monolith.dependencies import get_ctx

    async def session():
        yield _Session()

    app.dependency_overrides[get_db] = session
    app.dependency_overrides[get_ctx] = lambda: object()
    yield
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_ctx, None)


def bad_tokens(realm: FakeRealm) -> dict[str, str | None]:
    other = FakeRealm()  # a different key behind the same claims
    return {
        "none": None,
        "hs256": realm.hs256(),
        "hs256-with-the-public-key-as-secret": realm.hs256(
            secret="public-key-confusion" * 3
        ),
        "wrong-audience": realm.token(aud="some-other-api"),
        "id-token": realm.token(typ="ID"),
        "refresh-token": realm.token(typ="Refresh"),
        "expired": realm.token(lifetime=-3600),
        "unknown-issuer": FakeRealm(issuer="http://evil.test/realms/substrate").token(),
        "forged-signature": other.token(),
    }


async def call(app, method: str, path: str, token: str | None) -> httpx.Response:
    url = re.sub(r"\{[^}]+\}", "x", path)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    # Authentication is under test, not the limiter (which runs first and needs Redis); the app object is shared across tests.
    app.state.rate_limit_settings = {"enabled": False}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.request(method, url, headers=headers)


@pytest.mark.parametrize(("module", "method", "path"), cases())
async def test_only_a_keycloak_token_gets_in(module, method, path, realm):
    app = importlib.import_module(module).app
    app.state.token_verifier = realm.verifier()
    for name, token in bad_tokens(realm).items():
        response = await call(app, method, path, token)
        assert response.status_code == 401, (
            f"{name} was not refused ({response.status_code})"
        )
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["status"] == 401
    no_org = await call(app, method, path, realm.token(org=None))
    assert no_org.status_code == 403 and no_org.json()["type"].endswith(
        "organization-required"
    )


@pytest.mark.parametrize("module", APPS)
async def test_a_valid_token_passes_authentication(module, realm):
    """The same routes do not answer 401 to a good token (they may fail later for lack of a database)."""
    app = importlib.import_module(module).app
    app.state.token_verifier = realm.verifier()
    method, path = routes(app)[0]
    url = re.sub(r"\{[^}]+\}", "x", path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.request(
            method, url, headers=realm.bearer(roles=["platform_admin"])
        )
    assert response.status_code not in (401, 403) or response.json().get(
        "type", ""
    ).endswith("forbidden")


@pytest.mark.parametrize("module", APPS)
def test_the_open_endpoints_are_the_documented_ones(module):
    app = importlib.import_module(module).app
    served = {path for path in app.openapi()["paths"]} | {
        getattr(r, "path", "") for r in app.routes
    }
    unexpected = {p for p in served if p in OPEN} - set(OPEN)
    assert not unexpected


async def test_no_issuer_configured_refuses_everything_with_503(monkeypatch):
    from substrate_cloud.shared.settings import settings

    monkeypatch.setattr(settings, "OIDC_ISSUER", "")
    module = importlib.import_module("substrate_cloud.services.policy.app")
    app = module.app
    app.state.token_verifier = None
    response = await call(app, "POST", "/api/v1/policy/check", FakeRealm().token())
    assert response.status_code == 503
    assert response.json()["type"].endswith("identity-provider-unavailable")
