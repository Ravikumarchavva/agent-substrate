"""Sandbox data access when data and agents are on different machines: tokens, key publication, the gateway."""

from __future__ import annotations

import asyncio
import contextlib
import ssl
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import httpx
import jwt
import pytest
from _pki import issue, make_ca
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from substrate.types import RunScope

from substrate_cloud.data_access import (
    JWKS_PATH,
    DataGateway,
    DATA_SCOPES,
    DataTokenMinter,
    KeycloakMembership,
    PlatformDataAccess,
    build_data_access,
    load_keys,
    load_or_create_key,
    sandbox_has_client,
)
from substrate_cloud.monolith.routes.data_keys import router as data_keys_router

SCOPE = RunScope(
    tenant_id="acme", user_id="user-1", thread_id="thread-1", agent_id="analyst"
)


class Members:
    """The organization membership the minter consults: (tenant, user) pairs, and a record of every question."""

    def __init__(self, *pairs: tuple[str, str]):
        self.pairs = set(pairs)
        self.asked: list[tuple[str, str]] = []

    async def is_member(self, tenant_id: str, user_id: str) -> bool:
        self.asked.append((tenant_id, user_id))
        return (tenant_id, user_id) in self.pairs


def minter(members: Members | None = None, **kwargs) -> DataTokenMinter:
    return DataTokenMinter(
        Ed25519PrivateKey.generate(),
        issuer="agent-substrate",
        audience="substrate-data",
        membership=members or Members(("acme", "user-1")),
        **kwargs,
    )


def verify(m: DataTokenMinter, token: str) -> dict:
    public = jwt.PyJWK(m.jwks()["keys"][0]).key
    return jwt.decode(
        token,
        public,
        algorithms=["EdDSA"],
        audience="substrate-data",
        issuer="agent-substrate",
    )


# ── tokens ───────────────────────────────────────────────────────────────────


async def test_a_token_names_the_user_tenant_and_agent_and_can_only_read():
    m = minter()
    claims = verify(m, await m.mint(SCOPE, 90))
    assert claims["sub"] == "user-1" and claims["tenant_id"] == "acme"
    assert (
        "groups" not in claims
    )  # the data service derives the tenant from tenant_id; no group is asserted
    assert claims["scope"] == "data:read"
    assert (
        claims["act"] == {"sub": "agent-substrate:analyst"}
        and claims["thread_id"] == "thread-1"
    )
    assert claims["exp"] - claims["iat"] == 90
    assert jwt.get_unverified_header(await m.mint(SCOPE, 90))["kid"] == m.kid


async def test_a_files_token_is_delegated_writable_and_under_the_files_issuer():
    m = DataTokenMinter(
        Ed25519PrivateKey.generate(),
        issuer="agent-substrate",
        audience="substrate-data",
        files_issuer="agent-substrate-files",
    )
    public = jwt.PyJWK(m.jwks()["keys"][0]).key
    claims = jwt.decode(
        await m.mint_files(SCOPE),
        public,
        algorithms=["EdDSA"],
        audience="substrate-data",
        issuer="agent-substrate-files",
    )
    # The same key signs both; only the issuer name differs, because the data service trusts that name to write (or only to read).
    assert claims["scope"] == "files:write" and claims["sub"] == "user-1"
    assert (
        claims["act"] == {"sub": "agent-substrate:analyst"} and "groups" not in claims
    )
    assert verify(m, await m.mint(SCOPE, 90))["scope"] == "data:read"


async def test_a_files_token_needs_a_user_and_a_tenant():
    with pytest.raises(ValueError):
        await minter().mint_files(RunScope(tenant_id="acme"))
    with pytest.raises(ValueError):
        await minter().mint_files(RunScope(user_id="u"))


async def test_a_token_lives_about_as_long_as_the_run_and_never_longer_than_the_cap():
    m = minter()
    short, long = (
        verify(m, await m.mint(SCOPE, 5)),
        verify(m, await m.mint(SCOPE, 100_000)),
    )
    assert short["exp"] - short["iat"] == 60 and long["exp"] - long["iat"] == 330


@pytest.mark.parametrize(
    "scope",
    [RunScope(tenant_id="acme"), RunScope(user_id="u"), RunScope()],
)
async def test_no_user_or_no_tenant_means_no_token(scope):
    assert await minter().mint(scope, 60) is None


def test_the_signing_key_is_created_once_private_and_reused(tmp_path):
    path = tmp_path / "keys" / "data-token.pem"
    first = load_or_create_key(path)
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert (
        load_or_create_key(path).private_numbers()
        if hasattr(first, "private_numbers")
        else True
    )
    a = DataTokenMinter(first, issuer="i", audience="a", membership=Members())
    b = DataTokenMinter(
        load_or_create_key(path), issuer="i", audience="a", membership=Members()
    )
    assert (
        a.kid == b.kid
    )  # the same key after a restart, so the data service keeps trusting it


async def test_a_token_from_a_different_key_does_not_verify():
    a, b = minter(), minter()
    with pytest.raises(jwt.InvalidSignatureError):
        verify(a, await b.mint(SCOPE, 60))


# ── who the minter signs for ─────────────────────────────────────────────────


async def test_a_user_who_is_not_a_member_of_the_organization_gets_no_token():
    """A delegated run for a user outside the tenant (or one removed from it) is refused, not signed."""
    members = Members(("acme", "user-1"))
    m = minter(members)
    stranger = RunScope(
        tenant_id="acme", user_id="mallory", thread_id="t", agent_id="a"
    )
    other_org = RunScope(
        tenant_id="globex", user_id="user-1", thread_id="t", agent_id="a"
    )
    assert await m.mint(stranger, 60) is None
    assert await m.mint(other_org, 60) is None
    assert await m.mint(SCOPE, 60) is not None
    assert members.asked == [
        ("acme", "mallory"),
        ("globex", "user-1"),
        ("acme", "user-1"),
    ]


async def test_membership_is_asked_for_the_runs_own_principal_only():
    members = Members(("acme", "user-1"))
    await minter(members).mint(SCOPE, 60)
    assert members.asked == [("acme", "user-1")]


async def test_removing_the_user_stops_new_tokens():
    members = Members(("acme", "user-1"))
    m = minter(members)
    assert await m.mint(SCOPE, 60) is not None
    members.pairs.clear()
    assert await m.mint(SCOPE, 60) is None


async def test_a_token_carries_only_scopes_the_issuer_may_grant():
    m = minter(allowed_scopes=frozenset({"data:read", "files:read"}))
    claims = verify(m, await m.mint(SCOPE, 60, scopes=("files:read", "data:read")))
    assert claims["scope"] == "data:read files:read"
    for beyond in (("files:write",), ("documents:read",), ("admin:all",), ()):
        with pytest.raises(ValueError, match="allowed scopes"):
            await m.mint(SCOPE, 60, scopes=beyond)


def test_the_allowed_scopes_are_the_names_the_data_service_enforces():
    assert DATA_SCOPES == {
        "data:read",
        "files:read",
        "files:write",
        "documents:read",
        "documents:write",
        "memory:read",
        "memory:write",
    }
    with pytest.raises(ValueError, match="unknown data scopes"):
        minter(allowed_scopes=frozenset({"data:read", "files:*"}))


class FakeKeycloak:
    """Just enough of Keycloak: a token endpoint and the organization members endpoint."""

    def __init__(self):
        self.members = {("org-1", "user-1")}
        self.calls = 0
        self.fail = False
        self.clock = 0.0

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/protocol/openid-connect/token"):
            return httpx.Response(200, json={"access_token": "svc", "expires_in": 300})
        self.calls += 1
        if self.fail:
            return httpx.Response(500)
        assert request.headers["authorization"] == "Bearer svc"
        parts = request.url.path.split("/")
        org, user = parts[-3], parts[-1]
        assert request.url.path.startswith("/admin/realms/substrate/organizations/")
        return httpx.Response(200 if (org, user) in self.members else 404)


def keycloak_membership(fake: FakeKeycloak) -> KeycloakMembership:
    return KeycloakMembership(
        "https://id.example.com/realms/substrate",
        "cell",
        "secret",
        client=httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)),
        clock=lambda: fake.clock,
    )


async def test_keycloak_membership_is_cached_for_sixty_seconds():
    fake = FakeKeycloak()
    check = keycloak_membership(fake)
    assert await check.is_member("org-1", "user-1") and await check.is_member(
        "org-1", "user-1"
    )
    assert fake.calls == 1
    fake.members.clear()  # removed in Keycloak: still a member until the cache window ends
    assert await check.is_member("org-1", "user-1")
    fake.clock = 61
    assert not await check.is_member("org-1", "user-1")
    assert fake.calls == 2


async def test_keycloak_membership_fails_closed_and_does_not_cache_an_error():
    fake = FakeKeycloak()
    check = keycloak_membership(fake)
    fake.fail = True
    assert not await check.is_member("org-1", "user-1")
    fake.fail = False
    assert await check.is_member(
        "org-1", "user-1"
    )  # the outage was not remembered as "not a member"
    assert not await check.is_member("org-1", "mallory")
    assert not await check.is_member("org-9", "user-1")


def test_keycloak_membership_needs_a_realm_url():
    with pytest.raises(ValueError, match="realm"):
        KeycloakMembership("https://id.example.com", "c", "s")


def test_without_a_membership_check_no_data_access_is_built(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.setattr(
        "substrate_cloud.data_access.sandbox_has_client", lambda python_bin: True
    )
    access = build_data_access(
        service_url="https://data.example.com",
        data_dir=str(tmp_path),
        key_file="",
        relay_dir=str(tmp_path / "relay"),
        issuer="i",
        audience="a",
        python_bin=sys.executable,
    )
    assert access is None and "membership" in caplog.text


# ── key publication and rotation ────────────────────────────────────────────


async def test_tokens_carry_a_unique_id_and_are_not_valid_before_issue():
    m = minter()
    first, second = (
        verify(m, await m.mint(SCOPE, 60)),
        verify(m, await m.mint(SCOPE, 60)),
    )
    assert first["jti"] != second["jti"] and first["nbf"] == first["iat"]


async def test_rotation_publishes_both_keys_and_signs_with_the_new_one(tmp_path):
    path = tmp_path / "data-token.pem"
    old = load_or_create_key(path)
    path.rename(tmp_path / "data-token.previous.pem")  # the documented rotation step
    current, previous = load_keys(path)  # creates the new key, finds the old one
    members = Members(("acme", "user-1"))
    m = DataTokenMinter(
        current, issuer="i", audience="a", previous=previous, membership=members
    )
    published = {k["kid"] for k in m.jwks()["keys"]}
    assert published == {
        m.kid,
        DataTokenMinter(old, issuer="i", audience="a", membership=members).kid,
    }
    assert all("d" not in k for k in m.jwks()["keys"])  # public halves only
    assert jwt.get_unverified_header(await m.mint(SCOPE, 60))["kid"] == m.kid


async def test_the_public_keys_are_served_unauthenticated_and_404_when_off():
    app = FastAPI()
    app.include_router(data_keys_router)
    app.state.data_access = SimpleNamespace(minter=minter())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as client:
        ok = await client.get(JWKS_PATH)
        assert ok.status_code == 200 and "d" not in ok.json()["keys"][0]
        app.state.data_access = None
        assert (await client.get(JWKS_PATH)).status_code == 404


# ── gateway ──────────────────────────────────────────────────────────────────


class Upstream:
    """A stand-in data service on its own port, optionally over TLS / mutual TLS."""

    def __init__(
        self, *, tls: tuple[Path, Path] | None = None, client_ca: Path | None = None
    ):
        self.seen: list[dict] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def handle_any(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                owner.seen.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "body": body,
                    }
                )
                payload = b"A" * (3 << 20) if "big" in self.path else b'{"ok":true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Set-Cookie", "must=not-pass")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = do_DELETE = handle_any

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.scheme = "http"
        if tls:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(*map(str, tls))
            if client_ca:
                context.verify_mode = ssl.CERT_REQUIRED
                context.load_verify_locations(str(client_ca))
            self.server.socket = context.wrap_socket(
                self.server.socket, server_side=True
            )
            self.scheme = "https"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"{self.scheme}://127.0.0.1:{self.server.server_port}"

    def close(self) -> None:
        self.server.shutdown()


@pytest.fixture
def pki(tmp_path):
    ca, other = make_ca(tmp_path), make_ca(tmp_path, "Other CA")
    return {
        "ca": ca[0],
        "other_ca": other[0],
        "server": issue(tmp_path, ca, "server", ips=("127.0.0.1",)),
        "client": issue(tmp_path, ca, "gateway", client=True),
    }


@contextlib.asynccontextmanager
async def gateway(tmp_path, upstream: Upstream, **options):
    gate = DataGateway(tmp_path / "gw", upstream.url, **options)
    await gate.ensure_started()
    upstream.seen.clear()  # the startup probe is not part of the test
    transport = httpx.AsyncHTTPTransport(uds=str(gate.socket_path))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://substrate-data"
    ) as client:
        try:
            yield gate, client
        finally:
            await gate.stop()
            upstream.close()


async def test_allowed_reads_pass_with_the_token_and_without_ambient_headers(tmp_path):
    upstream = Upstream()
    async with gateway(tmp_path, upstream) as (gate, client):
        reply = await client.get(
            "/api/v1/search?q=taxi",
            headers={
                "Authorization": "Bearer t0k",
                "Cookie": "session=1",
                "X-Forwarded-For": "1.2.3.4",
            },
        )
        assert reply.status_code == 200 and reply.json() == {"ok": True}
        seen = upstream.seen[0]
        assert seen["path"] == "/api/v1/search?q=taxi"
        assert seen["headers"]["authorization"] == "Bearer t0k"
        assert (
            "cookie" not in seen["headers"] and "x-forwarded-for" not in seen["headers"]
        )
        assert seen["headers"]["host"] == upstream.url.removeprefix(
            "http://"
        )  # not "substrate-data"
        assert "set-cookie" not in reply.headers  # the sandbox sees no upstream cookies
        assert oct(gate.socket_path.stat().st_mode & 0o777) == "0o600"
        assert oct(gate.directory.stat().st_mode & 0o777) == "0o700"
    assert not gate.socket_path.exists()


async def test_statements_are_forwarded_with_their_body(tmp_path):
    upstream = Upstream()
    async with gateway(tmp_path, upstream) as (_, client):
        reply = await client.post(
            "/api/v1/statements",
            json={"sql": "select 1"},
            headers={"Accept": "application/vnd.apache.arrow.stream"},
        )
        assert reply.status_code == 200
        assert upstream.seen[0]["body"] == b'{"sql":"select 1"}'
        assert (
            upstream.seen[0]["headers"]["accept"]
            == "application/vnd.apache.arrow.stream"
        )
        assert (await client.post("/api/v1/statements/abc:cancel")).status_code == 200


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/v1/imports"),  # uploading is not something a sandbox does
        ("PATCH", "/api/v1/grants/schema/main.nyc"),
        ("DELETE", "/api/v1/catalogs/main"),
        ("POST", "/api/v1/catalogs/main/schemas/nyc/views"),
        ("GET", "/api/v1/compute/resources"),
        ("GET", "/internal/v1/health"),
        ("GET", "/api/v1/catalogs/main/schemas/../../../imports"),
        ("GET", "/api/v1/statements"),  # statement history is not part of a run's job
        ("GET", "/livez"),
    ],
)
async def test_anything_outside_the_allow_list_never_leaves_the_host(
    tmp_path, method, path
):
    upstream = Upstream()
    async with gateway(tmp_path, upstream) as (_, client):
        reply = await client.request(method, path)
        assert reply.status_code in {403, 404, 400}, reply.text
        assert upstream.seen == []


async def test_a_sandbox_cannot_choose_where_its_request_goes(tmp_path):
    upstream = Upstream()
    async with gateway(tmp_path, upstream) as (_, client):
        # An absolute-form request target and a Host header naming another machine change nothing.
        await client.get(
            "http://evil.example/api/v1/search", headers={"Host": "evil.example"}
        )
        assert [seen["headers"]["host"] for seen in upstream.seen] == [
            upstream.url.removeprefix("http://")
        ]


async def test_oversized_requests_are_refused_before_forwarding(tmp_path):
    upstream = Upstream()
    async with gateway(tmp_path, upstream) as (_, client):
        reply = await client.post("/api/v1/statements", content=b"x" * (2 << 20))
        assert reply.status_code == 413 and upstream.seen == []


async def test_large_responses_stream_through_intact(tmp_path):
    upstream = Upstream()
    async with gateway(tmp_path, upstream) as (_, client):
        reply = await client.get("/api/v1/search?q=big")
        assert reply.content == b"A" * (3 << 20)


async def test_a_dead_data_service_is_a_clear_502_not_a_hang(tmp_path):
    upstream = Upstream()
    url_port = upstream.server.server_port
    upstream.close()
    upstream.server.server_close()
    gate = DataGateway(
        tmp_path / "gw", f"http://127.0.0.1:{url_port}", connect_seconds=1
    )
    await gate.ensure_started()
    try:
        async with httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=str(gate.socket_path)),
            base_url="http://x",
        ) as client:
            reply = await client.get("/api/v1/search")
        assert reply.status_code == 502 and reply.headers["content-type"].startswith(
            "application/problem+json"
        )
    finally:
        await gate.stop()


async def test_over_tls_the_certificate_must_chain_to_the_configured_authority(
    tmp_path, pki
):
    upstream = Upstream(tls=pki["server"])
    async with gateway(tmp_path / "ok", upstream, ca_file=str(pki["ca"])) as (
        _,
        client,
    ):
        assert (await client.get("/api/v1/search")).status_code == 200
    upstream = Upstream(tls=pki["server"])
    async with gateway(tmp_path / "wrong", upstream, ca_file=str(pki["other_ca"])) as (
        _,
        client,
    ):
        assert (await client.get("/api/v1/search")).status_code == 502
        assert (
            upstream.seen == []
        )  # the token never went to a server we could not verify
    upstream = Upstream(tls=pki["server"])
    async with gateway(tmp_path / "system", upstream) as (
        _,
        client,
    ):  # system trust store: private CA unknown
        assert (await client.get("/api/v1/search")).status_code == 502


async def test_mutual_tls_presents_the_gateways_certificate(tmp_path, pki):
    upstream = Upstream(tls=pki["server"], client_ca=pki["ca"])
    async with gateway(tmp_path / "without", upstream, ca_file=str(pki["ca"])) as (
        _,
        client,
    ):
        assert (await client.get("/api/v1/search")).status_code == 502
    upstream = Upstream(tls=pki["server"], client_ca=pki["ca"])
    async with gateway(
        tmp_path / "with",
        upstream,
        ca_file=str(pki["ca"]),
        client_cert_file=str(pki["client"][0]),
        client_key_file=str(pki["client"][1]),
    ) as (_, client):
        assert (await client.get("/api/v1/search")).status_code == 200


def test_a_remote_data_service_must_use_https_and_paths_must_fit(tmp_path):
    with pytest.raises(ValueError, match="https"):
        DataGateway(tmp_path / "g", "http://data.example.com")
    DataGateway(tmp_path / "g", "https://data.example.com")
    DataGateway(tmp_path / "g", "http://127.0.0.1:8020")  # same machine, development
    with pytest.raises(ValueError, match="too long"):
        DataGateway(tmp_path / ("d" * 120), "https://data.example.com")
    with pytest.raises(ValueError):
        DataGateway(tmp_path / "g", "ftp://example.com")
    with pytest.raises(ValueError, match="go together"):
        DataGateway(
            tmp_path / "g", "https://data.example.com", client_cert_file="c.pem"
        )


async def test_too_many_concurrent_requests_get_a_503_with_retry_after(tmp_path):
    release = threading.Event()

    class Slow(Upstream):
        pass

    upstream = Upstream()
    original = upstream.server.RequestHandlerClass.handle_any

    def slow(self):
        if self.path.startswith("/api"):  # the startup probe answers at once
            release.wait(5)
        original(self)

    upstream.server.RequestHandlerClass.do_GET = slow
    async with gateway(tmp_path, upstream, max_connections=1) as (_, client):
        first = asyncio.create_task(client.get("/api/v1/search"))
        await asyncio.sleep(0.3)
        busy = await client.get("/api/v1/search")
        assert busy.status_code == 503 and busy.headers["retry-after"] == "1"
        release.set()
        assert (await first).status_code == 200


# ── the platform seam ────────────────────────────────────────────────────────


async def test_grant_issues_a_mount_with_this_runs_token_and_starts_the_relay_once(
    tmp_path,
):
    access = PlatformDataAccess(
        service_url=Upstream().url, relay_dir=tmp_path / "r", minter=minter()
    )
    try:
        a = await access.grant(SCOPE, timeout_s=60)
        b = await access.grant(SCOPE, timeout_s=60)
        assert a is not None and b is not None
        assert (
            a.socket_dir == str(tmp_path / "r")
            and (tmp_path / "r" / "api.sock").exists()
        )
        assert (
            verify(access.minter, a.token)["exp"]
            - verify(access.minter, a.token)["iat"]
            == 90
        )
        assert await access.grant(RunScope(), timeout_s=60) is None
        assert await access.token_for(SCOPE) is not None
    finally:
        await access.stop()
    assert not (tmp_path / "r" / "api.sock").exists()


def test_the_sandbox_interpreter_is_checked_for_the_client(tmp_path):
    assert sandbox_has_client(sys.executable) in {True, False}
    assert sandbox_has_client(str(tmp_path / "no-such-python")) is False


def test_nothing_is_built_without_a_data_service_or_without_the_client(
    tmp_path, caplog
):
    common = dict(
        data_dir=str(tmp_path),
        key_file="",
        relay_dir=str(tmp_path / "relay"),
        issuer="agent-substrate",
        audience="substrate-data",
        membership=Members(),
    )
    assert (
        build_data_access(service_url="", python_bin=sys.executable, **common) is None
    )
    assert not (tmp_path / "keys").exists()  # off means nothing is created
    assert (
        build_data_access(
            service_url="http://x", python_bin=str(tmp_path / "nope"), **common
        )
        is None
    )
    assert "cannot import substrate_data_client" in caplog.text


def test_enabling_data_access_needs_no_file_shared_with_the_data_service(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "substrate_cloud.data_access.sandbox_has_client", lambda python_bin: True
    )
    access = build_data_access(
        service_url="https://data.example.com",
        data_dir=str(tmp_path),
        key_file="",
        relay_dir=str(tmp_path / "relay"),
        issuer="agent-substrate",
        audience="substrate-data",
        python_bin=sys.executable,
        membership=Members(),
    )
    assert access is not None
    assert [p.name for p in (tmp_path / "keys").iterdir()] == [
        "data-token.pem"
    ]  # no exported trust file
    assert access.minter.jwks()["keys"][0]["kid"] == access.minter.kid


def test_a_remote_plaintext_data_service_is_refused_at_startup(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "substrate_cloud.data_access.sandbox_has_client", lambda python_bin: True
    )
    with pytest.raises(ValueError, match="https"):
        build_data_access(
            service_url="http://data.example.com",
            data_dir=str(tmp_path),
            key_file="",
            relay_dir=str(tmp_path / "relay"),
            issuer="i",
            audience="a",
            python_bin=sys.executable,
            membership=Members(),
        )
