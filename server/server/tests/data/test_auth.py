"""Access tokens (OIDC) and statement tickets (Ed25519)."""

from __future__ import annotations

import jwt
import pytest
from pydantic import SecretStr

from loamz.engine.errors import LoamzError
from loamz.server.data.kernel.auth import TokenVerifier
from loamz.server.data.kernel.settings import Settings
from loamz.server.data.kernel.tickets import TicketSigner, TicketVerifier
from loamz.server.identity import IdentityError
from tests.data.conftest import ISSUER


@pytest.fixture
def verifier(identity):
    return TokenVerifier(Settings(_env_file=None, oidc_issuer=ISSUER), jwks=identity.jwks)


def test_valid_token_maps_to_principal(identity, verifier):
    user = verifier.verify("Bearer " + identity.token("alice", ("data-admins", "analysts")))
    assert (user.id, user.is_admin, user.kind) == ("user:alice", True, "user")
    assert "group:analysts" in user.identities and "group:account users" in user.identities
    service = verifier.verify("Bearer " + identity.token("service-account-agent", ()))
    assert (service.id, service.is_admin) == ("service:agent", False)
    delegated = verifier.verify(
        "Bearer " + identity.token("bob", (), act={"sub": "service-account-loamz-agents"})
    )
    assert delegated.actor == "service-account-loamz-agents"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda i: i.token("alice", audience="someone-else"),
        lambda i: i.token("alice", issuer="https://evil.example/realms/x"),
        lambda i: i.token("alice", expires_in=-120),
        lambda i: i.token("alice", typ="Refresh"),
        lambda i: jwt.encode(
            {"iss": ISSUER, "aud": "loamz-data", "sub": "x"}, None, algorithm="none"
        ),
        lambda i: i.token("alice")[:-4] + "AAAA",
        lambda i: "",
    ],
)
def test_bad_tokens_are_rejected(identity, verifier, mutate):
    with pytest.raises(IdentityError) as caught:
        verifier.verify("Bearer " + mutate(identity))
    assert caught.value.status == 401


def test_tickets_sign_verify_tamper_expire_replay(ticket_keys):
    private, public = ticket_keys
    signer, verifier = TicketSigner(private, 60), TicketVerifier(public)
    ticket, ticket_id = signer.sign({"op": "query", "sub": "user:alice", "sql": "SELECT 1"})
    assert verifier.verify(ticket)["jti"] == ticket_id
    with pytest.raises(LoamzError, match="already used"):
        verifier.verify(ticket)
    header, payload, signature = signer.sign({"op": "query", "sub": "user:alice"})[0].split(".")
    forged = jwt.encode({"op": "query", "sub": "user:mallory"}, "x" * 32, algorithm="HS256")
    with pytest.raises(LoamzError):
        verifier.verify(".".join([header, forged.split(".")[1], signature]))
    expired = TicketSigner(private, 5)
    old, _ = expired.sign({"op": "query", "sub": "user:alice"})
    expired.ttl = -60
    stale, _ = expired.sign({"op": "query", "sub": "user:alice"})
    with pytest.raises(LoamzError):
        verifier.verify(stale)
    assert verifier.verify(old)["sub"] == "user:alice"


def test_access_token_is_not_a_ticket(identity, ticket_keys):
    with pytest.raises(LoamzError):
        TicketVerifier(ticket_keys[1]).verify(identity.token("alice"))


def test_settings_reject_insecure_remote_endpoints():
    with pytest.raises(ValueError):
        Settings(_env_file=None, iceberg_catalog_url="http://catalog.example.com")
    with pytest.raises(ValueError):
        Settings(_env_file=None, s3_endpoint_url="https://user:pw@s3.example.com")
    assert Settings(_env_file=None, s3_secret_access_key=SecretStr("x")).s3_endpoint_url
