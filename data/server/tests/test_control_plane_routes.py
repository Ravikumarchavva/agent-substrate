"""Control-plane routes that need no object storage or compute: the catalog's own lifecycle, who-am-I, the
overview, statement and import history, and the compute and job routes in front of a stand-in controller."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from conftest import ISSUER
from fastapi.testclient import TestClient
from pydantic import SecretStr

from loamz.data.server.app import create_app
from loamz.data.server.features.tables.iceberg import table_service
from loamz.data.server.kernel.auth import TokenVerifier
from loamz.data.server.kernel.container import Container
from loamz.data.server.kernel.settings import Settings

RESOURCE = "a" * 32


class Controller:
    """The compute controller's internal API, answered in process."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/internal/v1")
        self.calls.append((request.method, path))
        resource = {"id": RESOURCE, "name": "wh", "role": "sql", "state": "ready"}
        if path == "/resources" and request.method == "GET":
            return httpx.Response(200, json={"resources": [resource]})
        if path == f"/resources/{RESOURCE}" and request.method == "PUT":
            return httpx.Response(200, json={**resource, **json.loads(request.content)})
        if path == f"/resources/{RESOURCE}:stop":
            return httpx.Response(200, json={**resource, "state": "stopped"})
        if path == f"/resources/{RESOURCE}/events":
            return httpx.Response(200, json={"events": [{"state": "ready"}]})
        if path == "/jobs":
            return httpx.Response(200, json={"jobs": [{"id": "j1", "state": "running"}]})
        if path == "/jobs/j1":
            return httpx.Response(200, json={"id": "j1", "state": "running"})
        if path == "/jobs/j1:cancel":
            return httpx.Response(200, json={"id": "j1", "state": "cancelled"})
        return httpx.Response(404, json={"type": "x/not-found", "detail": "no such thing"})


class Tables:
    """The Iceberg side of a managed table, enough for its catalog lifecycle."""

    def create(self, record, arrow, partitioning):
        return SimpleNamespace(schema=arrow)

    def describe(self, table):
        columns = [{"name": f.name, "type": str(f.type).upper()} for f in table.schema]
        return {"columns": columns, "partitioning": []}

    def drop(self, record):
        return None


@pytest.fixture
def api(make_store, identity):
    settings = Settings(
        _env_file=None,
        oidc_issuer=ISSUER,
        controller_token=SecretStr("x" * 48),
        rate_limit_per_minute=0,
    )
    controller = Controller()
    container = Container(
        settings,
        verifier=TokenVerifier(settings, jwks=identity.jwks),
        controller_transport=httpx.MockTransport(controller),
    )
    container.store = make_store()
    container.store.ensure_default("data-admins")
    container.put(table_service, Tables())
    client = TestClient(create_app(container))
    client.headers["Authorization"] = "Bearer " + identity.token("alice", ("data-admins",))
    yield client, container, controller, identity
    client.close()


def test_a_catalog_is_updated_with_an_etag_and_deleted_when_empty(api):
    client, _, _, _ = api
    made = client.post("/api/v1/catalogs", json={"name": "sales", "comment": "v1"})
    assert made.status_code == 201 and made.headers["ETag"] == 'W/"1"'
    stale = client.patch(
        "/api/v1/catalogs/sales", json={"comment": "v2"}, headers={"If-Match": 'W/"7"'}
    )
    assert stale.status_code == 412
    updated = client.patch(
        "/api/v1/catalogs/sales", json={"comment": "v2"}, headers={"If-Match": 'W/"1"'}
    )
    assert updated.status_code == 200 and updated.json()["comment"] == "v2"
    client.post("/api/v1/catalogs/sales/schemas", json={"name": "s"})
    assert client.delete("/api/v1/catalogs/sales").status_code == 409  # not empty
    assert client.delete("/api/v1/catalogs/sales/schemas/s").status_code == 204
    assert client.delete("/api/v1/catalogs/sales").status_code == 204
    assert client.get("/api/v1/catalogs/sales").status_code == 404


def test_a_table_is_created_described_and_updated(api):
    client, _, _, _ = api
    body = {"name": "orders", "columns": [{"name": "id", "type": "BIGINT"}]}
    made = client.post("/api/v1/catalogs/main/schemas/default/tables", json=body)
    assert made.status_code == 201, made.text
    assert made.json()["full_name"] == "main.default.orders"
    updated = client.patch(
        "/api/v1/catalogs/main/schemas/default/tables/orders", json={"comment": "all orders"}
    )
    assert updated.status_code == 200 and updated.json()["comment"] == "all orders"
    bob = {"Authorization": "Bearer " + api[3].token("bob")}
    hidden = client.patch(
        "/api/v1/catalogs/main/schemas/default/tables/orders", json={"comment": "x"}, headers=bob
    )
    assert hidden.status_code == 404  # bob cannot even see it


def test_me_and_the_overview_describe_the_caller(api):
    client, container, _, _ = api
    me = client.get("/api/v1/me").json()
    assert me["principal"] == "user:alice" and me["is_admin"] is True
    container.store.record(
        "statements", {"principal": "user:alice", "sql": "SELECT 1", "status": "succeeded"}
    )
    overview = client.get("/api/v1/overview").json()
    assert overview["tables"] == 0 and [s["sql"] for s in overview["recent_statements"]] == [
        "SELECT 1"
    ]


def test_statement_and_import_history_belong_to_their_principal(api):
    client, container, _, identity = api
    row = container.store.record(
        "statements", {"principal": "user:alice", "sql": "SELECT 2", "status": "succeeded"}
    )
    assert client.get(f"/api/v1/statements/{row['id']}").json()["sql"] == "SELECT 2"
    bob = {"Authorization": "Bearer " + identity.token("bob")}
    assert client.get(f"/api/v1/statements/{row['id']}", headers=bob).status_code == 404
    assert client.get("/api/v1/imports").json()["imports"] == []


def test_compute_and_jobs_are_admin_routes_in_front_of_the_controller(api):
    client, _, controller, identity = api
    assert [r["id"] for r in client.get("/api/v1/compute/resources").json()["resources"]] == [
        RESOURCE
    ]
    spec = {"name": "wh", "role": "sql", "url": "http://127.0.0.1:9000", "token": "t" * 40}
    assert client.put(f"/api/v1/compute/resources/{RESOURCE}", json=spec).status_code == 200
    stopped = client.post(f"/api/v1/compute/resources/{RESOURCE}:stop")
    assert stopped.json()["state"] == "stopped"
    events = client.get(f"/api/v1/compute/resources/{RESOURCE}/events").json()
    assert events["events"] == [{"state": "ready"}]
    assert client.get("/api/v1/jobs").json()["jobs"][0]["id"] == "j1"
    assert client.get("/api/v1/jobs/j1").json()["state"] == "running"
    assert client.post("/api/v1/jobs/j1:cancel").json()["state"] == "cancelled"
    assert client.get("/api/v1/jobs/nope").status_code == 404  # the controller's answer, relayed
    bob = {"Authorization": "Bearer " + identity.token("bob")}
    assert client.get("/api/v1/jobs", headers=bob).status_code == 403
    assert client.get("/api/v1/compute/resources", headers=bob).json()["resources"] == []
