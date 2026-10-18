"""Data and agents run on different machines: trust is fetched, transport is TLS, history expires."""

from __future__ import annotations

import json
import socket
import ssl
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import jwt
import pytest
import uvicorn
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from pydantic import ValidationError
from substrate_identity import IdentityProviderUnavailable, InvalidToken, JwksSource

from conftest import IssuerKeys
from pki import issue, make_ca
from substrate_data.cli import listener_config
from substrate_data.core.models import Settings, TrustedIssuer


def test_a_trusted_issuer_is_capped_to_its_allowed_scopes(identity):
    from conftest import AGENT_ISSUER, ISSUER
    from substrate_data.auth.oidc import TokenVerifier

    cfg = Settings(
        _env_file=None,
        oidc_issuer=ISSUER,
        trusted_issuers=[
            TrustedIssuer(
                issuer=AGENT_ISSUER,
                jwks_uri=identity.agent_keys.uri,
                allowed_scopes=["data:read", "files:read"],
            )
        ],
    )
    verifier = TokenVerifier(cfg, jwks=identity.jwks)
    asked = identity.agent_token("alice", scope="data:read files:read files:write")
    caller = verifier.verify("Bearer " + asked)
    assert caller.scopes == {"data:read", "files:read"} and not caller.is_admin


def token(key, kid):
    now = int(time.time())
    return jwt.encode(
        {"sub": "u", "iat": now, "exp": now + 60}, key, algorithm="EdDSA", headers={"kid": kid}
    )


@pytest.fixture
def issuer():
    keys = IssuerKeys()
    first = Ed25519PrivateKey.generate()
    keys.publish("k1", first)
    yield keys, first
    keys.server.shutdown()


def remote(keys, **options):
    return JwksSource(keys.uri, ("EdDSA",), insecure_http=True, **options)


def test_keys_are_fetched_once_and_cached(issuer):
    keys, first = issuer
    fetcher = remote(keys)
    for _ in range(5):
        assert fetcher.key_for(token(first, "k1"))
    assert keys.hits == 1


def test_rotation_is_picked_up_without_coordination(issuer):
    keys, first = issuer
    fetcher = remote(keys, min_refresh=0)
    fetcher.key_for(token(first, "k1"))
    second = Ed25519PrivateKey.generate()
    keys.publish("k2", second)  # the issuer rotates; nobody tells the data service
    assert fetcher.key_for(token(second, "k2"))
    assert keys.hits == 2


def test_unknown_key_ids_cannot_make_the_service_hammer_the_issuer(issuer):
    keys, first = issuer
    fetcher = remote(keys, min_refresh=60)
    fetcher.key_for(token(first, "k1"))
    for index in range(20):
        with pytest.raises(InvalidToken):
            fetcher.key_for(token(first, f"spray-{index}"))
    assert keys.hits == 1


def test_a_brief_issuer_outage_is_survivable_a_long_one_is_not(issuer):
    keys, first = issuer
    fetcher = remote(keys, ttl=0, min_refresh=0, stale_seconds=3600)
    fetcher.key_for(token(first, "k1"))
    keys.failing = True
    assert fetcher.key_for(token(first, "k1"))  # cached keys keep working
    expired = remote(keys, ttl=0, min_refresh=0, stale_seconds=0.2)
    keys.failing = False
    expired.key_for(token(first, "k1"))
    keys.failing = True
    time.sleep(0.3)  # longer than the stale window
    with pytest.raises(IdentityProviderUnavailable) as error:
        expired.key_for(token(first, "k1"))
    assert error.value.status == 503


def test_no_keys_and_an_unreachable_issuer_is_a_503_not_a_pass(issuer):
    keys, first = issuer
    keys.failing = True
    with pytest.raises(IdentityProviderUnavailable) as error:
        remote(keys).key_for(token(first, "k1"))
    assert error.value.status == 503


def test_keys_for_algorithms_the_operator_did_not_allow_are_ignored(issuer):
    keys, first = issuer
    keys.keys["k1"]["alg"] = "RS256"
    with pytest.raises(IdentityProviderUnavailable):  # nothing usable was published
        remote(keys).key_for(token(first, "k1"))


def test_a_remote_issuer_must_use_https():
    with pytest.raises(ValidationError):
        TrustedIssuer(issuer="agent", jwks_uri="http://agent.example.com/jwks.json")
    TrustedIssuer(issuer="agent", jwks_uri="https://agent.example.com/jwks.json")
    TrustedIssuer(issuer="agent", jwks_uri="http://127.0.0.1:9000/jwks.json")  # loopback, tests/dev


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def pki(tmp_path):
    ca = make_ca(tmp_path)
    other_ca = make_ca(tmp_path, "Other CA")
    server = issue(tmp_path, ca, "server", dns=("data.example.test",), ips=("127.0.0.1",))
    client = issue(tmp_path, ca, "gateway", client=True)
    stranger = issue(tmp_path, other_ca, "stranger", client=True)
    return {
        "ca": ca[0],
        "other_ca": other_ca[0],
        "server": server,
        "client": client,
        "stranger": stranger,
    }


def test_the_issuers_keys_can_be_fetched_over_tls_from_a_private_ca(pki, issuer):
    keys, first = issuer
    body = json.dumps({"keys": list(keys.keys.values())}).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(*pki["server"])
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    uri = f"https://127.0.0.1:{server.server_port}/jwks.json"
    try:
        assert JwksSource(uri, ("EdDSA",), ca_file=pki["ca"]).key_for(token(first, "k1"))
        with pytest.raises(
            IdentityProviderUnavailable
        ):  # a CA we do not trust: keys are not accepted
            JwksSource(uri, ("EdDSA",), ca_file=pki["other_ca"]).key_for(token(first, "k1"))
    finally:
        server.shutdown()


def context(ca, client=None) -> ssl.SSLContext:
    """A client's TLS settings: the authority it trusts and, optionally, its own certificate."""
    built = ssl.create_default_context(cafile=str(ca))
    if client:
        built.load_cert_chain(*map(str, client))
    return built


def settings(**fields) -> Settings:
    return Settings(_env_file=None, **fields)


def test_plaintext_on_a_routable_address_is_refused():
    with pytest.raises(SystemExit):
        listener_config(settings(), FastAPI(), "0.0.0.0", 8020)
    listener_config(settings(), FastAPI(), "127.0.0.1", 8020)  # local development
    listener_config(
        settings(behind_tls_proxy=True), FastAPI(), "0.0.0.0", 8020
    )  # TLS ends at a mesh


def test_certificate_settings_are_all_or_nothing(pki):
    with pytest.raises(ValidationError):
        settings(tls_cert_file=pki["server"][0])
    with pytest.raises(ValidationError):
        settings(tls_client_ca_file=pki["ca"])


@pytest.fixture
def tls_api(pki):
    app = FastAPI()

    @app.get("/livez")
    def livez():
        return {"status": "ok"}

    port = free_port()
    cfg = settings(
        tls_cert_file=pki["server"][0],
        tls_key_file=pki["server"][1],
        tls_client_ca_file=pki["ca"],
    )
    server = uvicorn.Server(listener_config(cfg, app, "127.0.0.1", port))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    yield f"https://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(5)


def test_mutual_tls_admits_only_certificates_the_operator_signed(pki, tls_api):
    assert (
        httpx.get(tls_api + "/livez", verify=context(pki["ca"], pki["client"])).status_code == 200
    )
    with pytest.raises(httpx.HTTPError):  # no client certificate
        httpx.get(tls_api + "/livez", verify=context(pki["ca"]))
    with pytest.raises(httpx.HTTPError):  # a certificate from another authority
        httpx.get(tls_api + "/livez", verify=context(pki["ca"], pki["stranger"]))
    with pytest.raises(httpx.HTTPError):  # a client that does not trust our server certificate
        httpx.get(tls_api + "/livez", verify=context(pki["other_ca"], pki["client"]))


def test_tls_older_than_1_3_is_refused_by_default(pki, tls_api):
    old = context(pki["ca"], pki["client"])
    old.maximum_version = ssl.TLSVersion.TLSv1_2
    with pytest.raises(httpx.HTTPError):
        httpx.get(tls_api + "/livez", verify=old)


def test_history_past_retention_is_deleted_and_the_rest_kept(make_store):
    store = make_store()
    store.audit(None, "old.action", None, "ok")
    store.audit(None, "new.action", None, "ok")
    old = (datetime.now(UTC) - timedelta(days=400)).isoformat()
    with store.connection() as db:
        db.execute("UPDATE audit_events SET at=? WHERE action='old.action'", (old,))
        db.execute(
            "INSERT INTO statements (id, principal, sql, status, created_at) VALUES "
            "('s-old','user:a','select 1','succeeded',?), ('s-run','user:a','select 1','running',?)",
            (old, old),
        )
    assert store.purge_history(0) == {"audit_events": 0, "statements": 0}
    assert store.purge_history(365) == {"audit_events": 1, "statements": 1}  # a running one stays
    assert [e["action"] for e in store.audit_events(None, 10)] == ["new.action"]
