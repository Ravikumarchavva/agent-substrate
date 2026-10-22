from __future__ import annotations

import asyncio
import time

import jwt
import pytest

from loamz.server.identity import (
    AmbiguousOrganization,
    IdentityProviderUnavailable,
    InvalidToken,
    MissingToken,
    OrganizationError,
    PrimaryIssuer,
    TokenVerifier,
    TrustedIssuer,
    UntrustedIssuer,
    organization_of,
)
from tests.identity.conftest import AUDIENCE, ISSUER, Clock, KeyServer, mint


@pytest.fixture
def verifier(server, rsa_key):
    server.publish("k1", rsa_key)
    return TokenVerifier(PrimaryIssuer(ISSUER, AUDIENCE, jwks_url=server.url), insecure_http=True)


def bearer(token: str) -> str:
    return "Bearer " + token


def test_valid_user_token(verifier, rsa_key):
    who = verifier.verify(
        bearer(
            mint(
                rsa_key,
                email="a@example.com",
                groups=["/analysts"],
                realm_access={"roles": ["tenant_admin"]},
                scope="openid profile",
                jti="t-1",
                act={"sub": "service-account-loamz-agents"},
            )
        )
    )
    assert (who.subject, who.name, who.kind) == ("sub-alice", "alice", "user")
    assert (who.tenant_id, who.organization) == ("org-1", "acme")
    assert who.roles == {"tenant_admin"} and who.groups == {"analysts"}
    assert who.scopes is None  # an ordinary user token is not restricted
    assert who.actor == "service-account-loamz-agents" and who.token_id == "t-1"
    assert who.context == {"tenant_id": "org-1", "token_id": "t-1"}
    assert not who.trusted_issuer and who.email == "a@example.com"


def test_service_account_and_delegated_scopes(verifier, rsa_key):
    who = verifier.verify(
        bearer(
            mint(
                rsa_key,
                preferred_username="service-account-cell-admin",
                scope="openid data:read files:write",
                run_id="r1",
            )
        )
    )
    assert (who.kind, who.name) == ("service", "cell-admin")
    assert who.scopes == {"data:read", "files:write"}
    assert "openid" in who.token_scopes and who.context["run_id"] == "r1"


def test_audience_may_be_one_of_several(server, rsa_key):
    server.publish("k1", rsa_key)
    v = TokenVerifier(
        PrimaryIssuer(ISSUER, ["agents-api", "data-api"], jwks_url=server.url), insecure_http=True
    )
    assert v.verify(bearer(mint(rsa_key, audience=["other", "data-api"])))


def test_client_roles_of_the_audience(verifier, rsa_key):
    who = verifier.verify(
        bearer(
            mint(rsa_key, resource_access={AUDIENCE: {"roles": ["runner"]}, "x": {"roles": ["no"]}})
        )
    )
    assert who.roles == {"runner"}


@pytest.mark.parametrize(
    "build",
    [
        lambda k: mint(k, audience="someone-else"),
        lambda k: mint(k, issuer="https://evil.example/realms/x"),
        lambda k: mint(k, expires_in=-120),
        lambda k: mint(k, typ="Refresh"),
        lambda k: mint(k, typ=None),
        lambda k: mint(k, exp=None),
        lambda k: mint(k, sub=None),
        lambda k: mint(k, iat=None),
        lambda k: mint(k, aud=None, audience=None),
        lambda k: mint(k, kid="unknown"),
        lambda k: mint(k)[:-4] + "AAAA",
        lambda k: "not-a-jwt",
    ],
)
def test_bad_tokens_are_rejected(verifier, rsa_key, build):
    with pytest.raises(InvalidToken) as caught:
        verifier.verify(bearer(build(rsa_key)))
    assert caught.value.status == 401


def test_none_and_hmac_algorithms_are_rejected(verifier, server, rsa_key):
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "x",
        "iat": now,
        "exp": now + 60,
        "typ": "Bearer",
    }
    unsigned = jwt.encode(claims, None, algorithm="none", headers={"kid": "k1"})
    hs256 = jwt.encode(claims, "s" * 32, algorithm="HS256", headers={"kid": "k1"})
    for token in (unsigned, hs256):
        with pytest.raises(InvalidToken, match="algorithm not allowed"):
            verifier.verify(bearer(token))


def test_a_key_is_bound_to_its_algorithm(server, rsa_key, ed_key):
    server.publish("k1", rsa_key, alg="RS256")
    v = TokenVerifier(
        PrimaryIssuer(ISSUER, AUDIENCE, jwks_url=server.url, algorithms=["RS256", "EdDSA"]),
        insecure_http=True,
    )
    with pytest.raises(InvalidToken):
        v.verify(bearer(mint(ed_key, alg="EdDSA", kid="k1")))


def test_disallowed_algorithms_cannot_be_configured(server):
    with pytest.raises(ValueError):
        TokenVerifier(
            PrimaryIssuer(ISSUER, AUDIENCE, jwks_url=server.url, algorithms=["HS256"]),
            insecure_http=True,
        )


def test_missing_or_malformed_header(verifier):
    for header in (None, "", "Basic abc", "Bearer", "Bearer "):
        with pytest.raises(MissingToken):
            verifier.verify(header)


def test_plain_http_needs_the_explicit_flag(server):
    with pytest.raises(ValueError, match="https"):
        TokenVerifier(PrimaryIssuer(ISSUER, AUDIENCE, jwks_url=server.url))


def test_untrusted_issuer(verifier, rsa_key):
    with pytest.raises(UntrustedIssuer):
        verifier.verify(bearer(mint(rsa_key, issuer="https://other")))


def test_clock_leeway(verifier, rsa_key):
    assert verifier.verify(bearer(mint(rsa_key, expires_in=-10)))  # inside the 30 s leeway


# ------------------------------------------------------------------ organizations
@pytest.mark.parametrize(
    ("claim", "expected"),
    [
        ({"acme": {"id": "org-1"}}, ("org-1", "acme")),
        ({"acme": {}}, ("acme", "acme")),
        ({"acme": None}, ("acme", "acme")),
        (["acme"], ("acme", "acme")),
        ("acme", ("acme", "acme")),
        (None, (None, None)),
        ([], (None, None)),
        ({}, (None, None)),
    ],
)
def test_organization_formats(claim, expected):
    assert organization_of(claim) == expected


@pytest.mark.parametrize("claim", [["a", "b"], {"a": {"id": "1"}, "b": {"id": "2"}}])
def test_several_organizations_are_rejected(verifier, rsa_key, claim):
    with pytest.raises(AmbiguousOrganization) as caught:
        verifier.verify(bearer(mint(rsa_key, organization=claim)))
    assert (caught.value.status, caught.value.code) == (403, "ambiguous-organization")


@pytest.mark.parametrize("claim", [None, [], {}])
def test_no_organization_is_rejected_when_required(verifier, rsa_key, claim):
    with pytest.raises(OrganizationError) as caught:
        verifier.verify(bearer(mint(rsa_key, organization=claim)))
    assert (caught.value.status, caught.value.code) == (403, "organization-required")


def test_organization_optional_when_not_required(server, rsa_key):
    server.publish("k1", rsa_key)
    v = TokenVerifier(
        PrimaryIssuer(ISSUER, AUDIENCE, jwks_url=server.url),
        require_organization=False,
        insecure_http=True,
    )
    assert v.verify(bearer(mint(rsa_key, organization=None))).tenant_id is None
    with pytest.raises(AmbiguousOrganization):  # still never several
        v.verify(bearer(mint(rsa_key, organization=["a", "b"])))


def test_malformed_organization_claim(verifier, rsa_key):
    with pytest.raises(OrganizationError):
        verifier.verify(bearer(mint(rsa_key, organization=[1, 2])))


# ------------------------------------------------------------------ JWKS behaviour
def make(server, clock, **options):
    return TokenVerifier(
        PrimaryIssuer(ISSUER, AUDIENCE, jwks_url=server.url),
        insecure_http=True,
        clock=clock,
        **options,
    )


def test_keys_are_cached(server, rsa_key):
    server.publish("k1", rsa_key)
    v = make(server, Clock())
    for _ in range(5):
        v.verify(bearer(mint(rsa_key)))
    assert server.hits == 1


def test_rotation_refreshes_on_unknown_kid_but_is_throttled(server, rsa_key, ed_key):
    clock = Clock()
    server.publish("k1", rsa_key)
    v = make(server, clock)
    v.verify(bearer(mint(rsa_key)))
    server.publish("k2", ed_key, alg="EdDSA")
    # Throttled: a second key endpoint read inside 30 s does not happen, so the new key is not seen yet.
    with pytest.raises(InvalidToken):
        v.verify(bearer(mint(ed_key, alg="EdDSA", kid="k2")))
    assert server.hits == 1
    clock.now += 31
    assert v.verify(bearer(mint(ed_key, alg="EdDSA", kid="k2")))
    assert server.hits == 2


def test_random_kids_cannot_amplify_requests(server, rsa_key):
    server.publish("k1", rsa_key)
    v = make(server, Clock())
    v.verify(bearer(mint(rsa_key)))
    for index in range(25):
        with pytest.raises(InvalidToken):
            v.verify(bearer(mint(rsa_key, kid=f"spray-{index}")))
    assert server.hits == 1


def test_stale_if_error_then_fail_closed(server, rsa_key):
    clock = Clock()
    server.publish("k1", rsa_key)
    v = make(server, clock)
    v.verify(bearer(mint(rsa_key)))
    server.failing = True
    clock.now += 400  # past the 300 s ttl: a refresh is attempted and fails
    assert v.verify(bearer(mint(rsa_key)))  # cached keys keep working
    clock.now += 3000  # 3400 s since the last good fetch: still inside the hour
    assert v.verify(bearer(mint(rsa_key)))
    clock.now += 400  # beyond 1 h
    with pytest.raises(IdentityProviderUnavailable) as caught:
        v.verify(bearer(mint(rsa_key)))
    assert caught.value.status == 503


def test_unreachable_issuer_with_no_cache_is_503_not_a_pass(server, rsa_key):
    server.failing = True
    v = make(server, Clock())
    with pytest.raises(IdentityProviderUnavailable):
        v.verify(bearer(mint(rsa_key)))


def test_recovery_after_outage(server, rsa_key):
    clock = Clock()
    server.failing = True
    v = make(server, clock)
    with pytest.raises(IdentityProviderUnavailable):
        v.verify(bearer(mint(rsa_key)))
    server.failing = False
    server.publish("k1", rsa_key)
    clock.now += 31
    assert v.verify(bearer(mint(rsa_key)))


def test_enc_keys_and_unusable_keys_are_skipped(server, rsa_key):
    server.publish("k1", rsa_key)
    server.keys["enc"] = {**server.keys["k1"], "kid": "enc", "use": "enc", "alg": "RSA-OAEP"}
    server.keys["junk"] = {"kid": "junk", "kty": "RSA", "n": "!", "e": "AQAB"}
    assert make(server, Clock()).verify(bearer(mint(rsa_key)))


def test_discovery(server, rsa_key):
    server.publish("k1", rsa_key)
    # Discovery is read from {issuer}/.well-known/...: point the issuer at the loopback server.
    issuer = server.base + "/realms/loamz"
    server.discovery_issuer = issuer
    v = TokenVerifier(PrimaryIssuer(issuer, AUDIENCE), insecure_http=True)
    assert v.verify(bearer(mint(rsa_key, issuer=issuer)))


def test_discovery_must_name_the_same_issuer(server, rsa_key):
    server.publish("k1", rsa_key)
    issuer = server.base + "/realms/loamz"  # the document still says ISSUER
    v = TokenVerifier(PrimaryIssuer(issuer, AUDIENCE), insecure_http=True)
    with pytest.raises(IdentityProviderUnavailable):
        v.verify(bearer(mint(rsa_key, issuer=issuer)))


def test_async_verification(verifier, rsa_key):
    async def run():
        return await verifier.averify(bearer(mint(rsa_key)))

    assert asyncio.run(run()).tenant_id == "org-1"


# ------------------------------------------------------------------ trusted issuers
def trusted(server, **options):
    return TrustedIssuer("agent", "data-api", server.url, **options)


def agent_token(ed_key, **extra):
    claims = {
        "scope": "data:read",
        "tenant_id": "acme",
        "thread_id": "th-1",
        "groups": ["tenant-acme"],
        "act": {"sub": "loamz-agents:primary"},
        **extra,
    }
    return mint(
        ed_key,
        alg="EdDSA",
        kid="agent-1",
        issuer="agent",
        audience="data-api",
        typ=None,
        organization=None,
        preferred_username=None,
        sub="alice",
        **claims,
    )


def test_trusted_issuer_token(server, ed_key):
    server.publish("agent-1", ed_key, alg="EdDSA")
    v = TokenVerifier(trusted=[trusted(server)], insecure_http=True)
    who = v.verify(bearer(agent_token(ed_key, jti="j1")))
    assert who.trusted_issuer and who.kind == "user" and who.name == "alice"
    assert who.scopes == {"data:read"} and who.roles == frozenset()
    assert who.tenant_id == "acme" and who.actor == "loamz-agents:primary"
    assert who.groups == {"tenant-acme"}
    assert who.context == {"tenant_id": "acme", "thread_id": "th-1", "token_id": "j1"}


def test_trusted_issuer_without_scope_gets_the_default(server, ed_key):
    server.publish("agent-1", ed_key, alg="EdDSA")
    v = TokenVerifier(trusted=[trusted(server)], insecure_http=True)
    assert v.verify(bearer(agent_token(ed_key, scope=None))).scopes == {"data:read"}


def test_trusted_issuer_scopes_are_capped(server, ed_key):
    server.publish("agent-1", ed_key, alg="EdDSA")
    v = TokenVerifier(
        trusted=[trusted(server, allowed_scopes=frozenset({"data:read", "files:read"}))],
        insecure_http=True,
    )
    who = v.verify(
        bearer(agent_token(ed_key, scope="data:read files:read files:write memory:write"))
    )
    assert who.scopes == {"data:read", "files:read"}
    nothing = v.verify(bearer(agent_token(ed_key, scope="memory:write")))
    assert nothing.scopes == frozenset()  # least privilege, never an unrestricted token


def test_trusted_issuer_rejections(server, ed_key, rsa_key):
    server.publish("agent-1", ed_key, alg="EdDSA")
    v = TokenVerifier(trusted=[trusted(server)], insecure_http=True)
    for bad in (
        agent_token(ed_key, expires_in=-120),
        mint(ed_key, alg="EdDSA", kid="agent-1", issuer="agent", audience="agents-api"),
        mint(ed_key, alg="EdDSA", kid="forged", issuer="agent", audience="data-api"),
    ):
        with pytest.raises(InvalidToken):
            v.verify(bearer(bad))
    # The primary realm's issuer is not configured here at all.
    with pytest.raises(UntrustedIssuer):
        v.verify(bearer(mint(rsa_key)))


def test_trusted_issuer_algorithms_are_pinned(server, rsa_key):
    server.publish("k1", rsa_key)  # an RS256 key, but the operator allowed only EdDSA
    v = TokenVerifier(trusted=[trusted(server)], insecure_http=True)
    with pytest.raises(InvalidToken, match="algorithm not allowed"):
        v.verify(bearer(mint(rsa_key, issuer="agent", audience="data-api")))


def test_a_trusted_issuers_key_cannot_sign_for_the_primary_issuer(server, ed_key, rsa_key):
    realm = KeyServer()
    try:
        realm.publish("k1", rsa_key)
        server.publish("agent-1", ed_key, alg="EdDSA")
        v = TokenVerifier(
            PrimaryIssuer(ISSUER, AUDIENCE, jwks_url=realm.url),
            [trusted(server)],
            insecure_http=True,
        )
        # Signed with the agent's key but claiming the realm's issuer: verified against the realm's keys.
        with pytest.raises(InvalidToken):
            v.verify(bearer(mint(ed_key, alg="EdDSA", kid="agent-1")))
    finally:
        realm.close()


def test_platform_service_accounts_need_no_organization(verifier, rsa_key, server):
    token = mint(
        rsa_key,
        organization=None,
        preferred_username="service-account-cell-admin",
        resource_access={AUDIENCE: {"roles": ["provision-tenant", "erase-tenant"]}},
    )
    who = verifier.verify(bearer(token))
    assert (who.kind, who.tenant_id, who.name) == ("service", None, "cell-admin")
    assert who.roles == {"provision-tenant", "erase-tenant"}
    strict = TokenVerifier(
        PrimaryIssuer(ISSUER, AUDIENCE, jwks_url=server.url),
        services_without_organization=False,
        insecure_http=True,
    )
    with pytest.raises(OrganizationError):
        strict.verify(bearer(token))
