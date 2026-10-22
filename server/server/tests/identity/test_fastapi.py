from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from loamz.server.identity import Identity, PrimaryIssuer, TokenVerifier
from loamz.server.identity.fastapi import identity_dependency, require_roles, require_scopes
from tests.identity.conftest import AUDIENCE, ISSUER, mint


@pytest.fixture
def client(server, rsa_key):
    server.publish("k1", rsa_key)
    verifier = TokenVerifier(
        PrimaryIssuer(ISSUER, AUDIENCE, jwks_url=server.url), insecure_http=True
    )
    current = identity_dependency(verifier)
    app = FastAPI()

    @app.get("/me")
    async def me(who: Identity = Depends(current)):
        return {"tenant": who.tenant_id, "name": who.name}

    @app.get("/admin")
    async def admin(who: Identity = Depends(require_roles(current, "tenant_admin"))):
        return {"ok": True}

    @app.get("/files")
    async def files(who: Identity = Depends(require_scopes(current, "files:read"))):
        return {"ok": True}

    return TestClient(app)


def get(client, path, token=None):
    return client.get(path, headers={"Authorization": f"Bearer {token}"} if token else {})


def test_identity_and_errors(client, rsa_key):
    ok = get(client, "/me", mint(rsa_key))
    assert ok.json() == {"tenant": "org-1", "name": "alice"}
    missing = get(client, "/me")
    assert missing.status_code == 401 and "Bearer" in missing.headers["www-authenticate"]
    assert get(client, "/me", mint(rsa_key, audience="x")).status_code == 401
    several = get(client, "/me", mint(rsa_key, organization=["a", "b"]))
    assert several.status_code == 403
    assert several.json()["detail"]["code"] == "ambiguous-organization"


def test_roles(client, rsa_key):
    assert get(client, "/admin", mint(rsa_key)).status_code == 403
    assert (
        get(client, "/admin", mint(rsa_key, realm_access={"roles": ["tenant_admin"]})).status_code
        == 200
    )


def test_scopes(client, rsa_key):
    assert get(client, "/files", mint(rsa_key)).status_code == 200  # ordinary user token
    assert get(client, "/files", mint(rsa_key, scope="files:read")).status_code == 200
    assert get(client, "/files", mint(rsa_key, scope="data:read")).status_code == 403
