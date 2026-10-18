"""Agent access: tokens minted by another system, read-only by construction, found and audited."""

from __future__ import annotations

import threading
import time

import httpx
import polars as pl
import pytest
import uvicorn
from conftest import ADMIN, AGENT_ISSUER
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import loamz.data.client as sd
from loamz.data.client import DataClient, DataError, ResultTooLargeError

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def orders(stack, tmp_path_factory):
    admin = stack.client("alice", *ADMIN)
    admin.create_schema("main", "agents")
    path = tmp_path_factory.mktemp("agents") / "orders.parquet"
    pl.DataFrame(
        {"region": ["north", "south"] * 500, "amount": [float(i % 50) for i in range(1000)]}
    ).write_parquet(path)
    admin.upload("main.agents.orders", [path])
    with stack.http("alice", *ADMIN) as http:
        http.patch(
            "/grants/schema/main.agents",
            json={"changes": [{"principal": "group:account users", "add": ["USE_SCHEMA"]}]},
        )


def grant_tenant(stack, privilege="SELECT"):
    with stack.http("alice", *ADMIN) as http:
        http.patch(
            "/grants/schema/main.agents",
            json={"changes": [{"principal": "group:tenant-acme", "add": [privilege]}]},
        )


def revoke_tenant(stack):
    with stack.http("alice", *ADMIN) as http:
        http.patch(
            "/grants/schema/main.agents",
            json={"changes": [{"principal": "group:tenant-acme", "remove": ["SELECT"]}]},
        )


def test_agent_tokens_follow_grants_and_are_audited(stack, orders):
    agent = stack.agent_client("user-42", "acme", run_id="run-9", thread_id="thread-7")
    with pytest.raises(DataError) as caught:
        agent.sql("SELECT count(*) FROM main.agents.orders")
    assert caught.value.status == 404  # no grant yet: the table is invisible
    assert agent.search("orders") == []
    grant_tenant(stack)
    try:
        assert agent.sql("SELECT count(*) AS n FROM main.agents.orders").item() == 1000
        found = agent.search("orders")
        assert [r["full_name"] for r in found] == ["main.agents.orders"]
        assert found[0]["columns"] == ["region", "amount"] and found[0]["row_count"] == 1000
        assert agent.search("amount")[0]["kind"] == "table"  # matches on a column name
        assert agent.search("zzz-nothing") == []
        info = agent.describe("main.agents.orders")
        assert info["kind"] == "table" and info["sample"].height == 5 and info["row_count"] == 1000
        # Audited with the user, the acting agent and where the request came from.
        history = agent.request("GET", "/statements")["statements"][0]
        assert history["principal"] == "user:user-42"
        assert history["actor"] == "loamz-agents:primary"
        assert history["context"] == {
            "tenant_id": "acme",
            "thread_id": "thread-7",
            "run_id": "run-9",
        }
    finally:
        revoke_tenant(stack)
    with pytest.raises(DataError) as caught:
        agent.sql("SELECT count(*) FROM main.agents.orders")
    assert caught.value.status == 404


def test_agent_tokens_cannot_change_anything(stack, orders):
    grant_tenant(stack, "MODIFY")
    agent = stack.agent_client("user-42")
    try:
        for method, path, body in [
            (
                "POST",
                "/imports",
                {"table": "main.agents.t", "files": [{"name": "a.csv", "size": 1}]},
            ),
            ("POST", "/catalogs/main/schemas", {"name": "mine"}),
            ("POST", "/catalogs/main/schemas/agents/views", {"name": "v", "sql": "SELECT 1"}),
            ("DELETE", "/catalogs/main/schemas/agents/tables/orders", None),
            (
                "PATCH",
                "/grants/schema/main.agents",
                {"changes": [{"principal": "user:x", "add": ["SELECT"]}]},
            ),
            (
                "POST",
                "/pipelines",
                {"name": "p", "source": "main.agents.orders", "target": "main.agents.o2"},
            ),
            (
                "POST",
                "/compute/resources",
                {"name": "x", "role": "sql", "url": "http://127.0.0.1:1"},
            ),
        ]:
            with pytest.raises(DataError) as caught:
                agent.request(method, path, json=body)
            assert caught.value.status == 403 and caught.value.code == "forbidden", path
    finally:
        revoke_tenant(stack)
    admin = stack.client("alice", *ADMIN)
    assert admin.sql("SELECT count(*) FROM agents.orders").item() == 1000  # nothing was touched


def test_agents_can_cancel_their_own_statements(stack, orders):
    grant_tenant(stack)
    try:
        agent = stack.agent_client("user-42")
        agent.sql("SELECT 1")
        assert agent.request("POST", f"/statements/{'a' * 32}:cancel") == {"interrupted": False}
    finally:
        revoke_tenant(stack)


@pytest.mark.parametrize(
    "case",
    ["unknown-issuer", "forged-key", "wrong-audience", "expired", "admin-claim", "alg-none"],
)
def test_untrusted_agent_tokens_are_rejected(stack, case):
    identity = stack.identity
    wrong_key = Ed25519PrivateKey.generate()
    token = {
        "unknown-issuer": lambda: identity.agent_token("u", issuer="someone-else"),
        "forged-key": lambda: identity.agent_token("u", key=wrong_key),
        "wrong-audience": lambda: identity.agent_token("u", audience="other"),
        "expired": lambda: identity.agent_token("u", expires_in=-120),
        "admin-claim": lambda: identity.agent_token("u", groups=["data-admins"], scope="data:read"),
        "alg-none": lambda: __import__("jwt").encode(
            {"iss": AGENT_ISSUER, "aud": "loamz-data", "sub": "u"}, None, algorithm="none"
        ),
    }[case]()
    response = httpx.get(
        stack.api + "/api/v1/catalogs", headers={"Authorization": "Bearer " + token}
    )
    if case == "admin-claim":
        # A trusted issuer can claim any group, but `data-admins` must never make an agent admin.
        assert response.status_code == 200
        assert (
            httpx.get(
                stack.api + "/api/v1/audit-events", headers={"Authorization": "Bearer " + token}
            ).status_code
            == 403
        )
    else:
        assert response.status_code == 401, response.text


def test_large_arrow_results_stream_intact(stack, taxi_like):
    """Regression: results larger than one HTTP chunk must arrive whole through stream()/sql()."""
    admin = stack.client("alice", *ADMIN)
    frame = admin.sql("SELECT * FROM agents.big")
    assert frame.height == 200_000
    assert frame["v"].sum() == sum(range(200_000))
    assert sum(b.num_rows for b in admin.stream("SELECT * FROM agents.big")) == 200_000


@pytest.fixture(scope="module")
def taxi_like(stack, orders, tmp_path_factory):
    path = tmp_path_factory.mktemp("big") / "big.parquet"
    pl.DataFrame({"v": range(200_000), "w": [f"row-{i}" for i in range(200_000)]}).write_parquet(
        path
    )
    stack.client("alice", *ADMIN).upload("main.agents.big", [path])


def test_the_client_refuses_to_pull_huge_results_into_memory(stack, orders):
    admin = stack.client("alice", *ADMIN)
    with pytest.raises(ResultTooLargeError) as caught:
        admin.sql("SELECT * FROM agents.orders", max_bytes=1000)
    assert caught.value.status == 413 and "Aggregate" in str(caught.value)
    assert admin.sql("SELECT count(*) AS n FROM agents.orders", max_bytes=1000).item() == 1000
    batches = list(admin.stream("SELECT * FROM agents.orders"))
    assert sum(b.num_rows for b in batches) == 1000


class UnixServer:
    """Serves the API on a Unix socket, as the sandbox gateway does for a jail."""

    def __init__(self, app, path):
        self.server = uvicorn.Server(uvicorn.Config(app, uds=str(path), log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 20
        while not self.server.started and time.monotonic() < deadline:
            time.sleep(0.02)

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=10)


def test_sandbox_discovery_over_a_unix_socket(stack, orders, tmp_path, monkeypatch):
    """The in-sandbox path: a socket and a token file, nothing else, no network."""
    grant_tenant(stack)
    from loamz.data.server.app import create_app
    from loamz.data.server.kernel.auth import TokenVerifier
    from loamz.data.server.kernel.container import Container

    container = Container(
        stack.settings, verifier=TokenVerifier(stack.settings, jwks=stack.identity.jwks)
    )
    server = UnixServer(create_app(container), tmp_path / "api.sock")
    (tmp_path / "token").write_text(stack.identity.agent_token("user-42") + "\n")
    monkeypatch.setattr(sd, "SANDBOX_DIR", tmp_path)
    monkeypatch.setattr(sd, "_default", None)
    try:
        client = sd.connect()
        assert client.sql("SELECT count(*) AS n FROM main.agents.orders").item() == 1000
        # The module-level helpers an agent actually writes:
        frame = sd.sql(
            "SELECT region, sum(amount) AS total FROM main.agents.orders GROUP BY 1 ORDER BY 1"
        )
        assert frame.columns == ["region", "total"] and frame.height == 2
        assert sd.search("orders")[0]["full_name"] == "main.agents.orders"
        assert sd.describe("main.agents.orders", sample=2)["sample"].height == 2
        # A token file that stops working (run over) stops all access immediately.
        (tmp_path / "token").write_text(stack.identity.agent_token("user-42", expires_in=-120))
        with pytest.raises(DataError) as caught:
            sd.sql("SELECT 1")
        assert caught.value.status == 401
    finally:
        server.stop()
        revoke_tenant(stack)


def test_no_connection_outside_a_sandbox_is_a_clear_error(monkeypatch, tmp_path):
    monkeypatch.setattr(sd, "SANDBOX_DIR", tmp_path / "missing")
    monkeypatch.delenv("LOAMZ_DATA_URL", raising=False)
    with pytest.raises(RuntimeError, match="not in a sandbox"):
        sd.connect()
    assert isinstance(sd.connect("http://127.0.0.1:1", "t"), DataClient)
