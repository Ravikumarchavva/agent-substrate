"""Delegated tokens may reach only routes that declare a scope they hold."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from loamz.server.data.kernel import problems as http
from loamz.server.data.kernel.http import principal
from loamz.server.data.kernel.principal import Principal
from loamz.server.data.kernel.scopes import require_scope
from loamz.server.data.kernel.settings import Settings

TOKENS = {
    "user": Principal("u", "alice", "user"),  # ordinary: grants only
    "reader": Principal("a", "agent", "user", read_only=True, scopes=frozenset({"data:read"})),
    "filer": Principal("a", "agent", "user", scopes=frozenset({"data:read", "files:write"})),
    "docs": Principal("a", "agent", "user", scopes=frozenset({"documents:read"})),
}


class Verifier:
    def verify(self, authorization):
        return TOKENS[authorization.split()[-1]]


@pytest.fixture
def client():
    app = FastAPI()
    http.install(app, Settings(_env_file=None))
    app.state.container = SimpleNamespace(
        verifier=Verifier(), limiter=SimpleNamespace(check=lambda _id: None)
    )

    @app.get("/api/v1/browse")
    def browse(caller: Principal = Depends(principal)):
        return {"ok": True}

    @app.post("/api/v1/mutate")
    def mutate(caller: Principal = Depends(principal)):
        return {"ok": True}

    @app.post("/api/v1/files", dependencies=[require_scope("files:write")])
    def files():
        return {"ok": True}

    @app.get("/api/v1/files", dependencies=[require_scope("files:read", "files:write")])
    def list_files():
        return {"ok": True}

    return TestClient(app)


def call(client, method, path, who):
    return client.request(method, path, headers={"Authorization": f"Bearer {who}"})


def test_ordinary_user_tokens_are_limited_by_grants_not_scopes(client):
    for method, path in (
        ("GET", "/api/v1/browse"),
        ("POST", "/api/v1/mutate"),
        ("POST", "/api/v1/files"),
    ):
        assert call(client, method, path, "user").status_code == 200


def test_a_read_only_token_browses_but_cannot_mutate_or_use_file_routes(client):
    assert call(client, "GET", "/api/v1/browse", "reader").status_code == 200
    assert call(client, "POST", "/api/v1/mutate", "reader").json()["type"].endswith("forbidden")
    assert call(client, "POST", "/api/v1/files", "reader").status_code == 403
    assert (
        call(client, "GET", "/api/v1/files", "reader").json()["type"].endswith("insufficient-scope")
    )


def test_a_scoped_token_reaches_only_routes_declaring_its_scope(client):
    assert call(client, "POST", "/api/v1/files", "filer").status_code == 200
    assert (
        call(client, "GET", "/api/v1/files", "filer").status_code == 200
    )  # "files:read" OR "files:write"
    rejected = call(
        client, "POST", "/api/v1/mutate", "filer"
    )  # a route that declares nothing is closed
    assert rejected.status_code == 403 and rejected.json()["type"].endswith("insufficient-scope")
    assert call(client, "GET", "/api/v1/browse", "filer").status_code == 200


def test_the_wrong_scope_is_refused_and_says_what_was_needed(client):
    refused = call(client, "POST", "/api/v1/files", "docs")
    assert refused.status_code == 403 and refused.json()["required"] == ["files:write"]
