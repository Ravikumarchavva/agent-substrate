"""Real JWT round-trip for the new daily_message_limit claim — proves it
survives encode -> decode via verify_token(), not just that the AuthClaims
model accepts the field in isolation."""

from __future__ import annotations

from substrate.serving.shared.auth.jwt import create_access_token, verify_token

SECRET = "test-secret-not-for-production"


def test_daily_message_limit_round_trips_through_a_real_token():
    token, _ = create_access_token(
        "proj-owner",
        SECRET,
        tenant_id="proj-abc",
        extra={"daily_message_limit": 5},
    )
    claims = verify_token(token, SECRET)
    assert claims is not None
    assert claims.tenant_id == "proj-abc"
    assert claims.daily_message_limit == 5


def test_daily_message_limit_defaults_to_none_when_absent():
    """A token with no `extra` at all must still decode cleanly — the new
    field must not become a required claim (tenant_id, unlike it, is
    required — see test_missing_tenant_id_is_rejected below)."""
    token, _ = create_access_token("user-1", SECRET, tenant_id="proj-abc")
    claims = verify_token(token, SECRET)
    assert claims is not None
    assert claims.daily_message_limit is None
    assert claims.tenant_id == "proj-abc"


def test_missing_tenant_id_is_rejected():
    """A missing tenant used to silently become "default", joining unrelated
    projects into the same authorization and storage namespace — a token
    without one must now be rejected outright, not decoded with a fallback."""
    import jwt as pyjwt

    token = pyjwt.encode({"sub": "user-1", "type": "access"}, SECRET, algorithm="HS256")
    assert verify_token(token, SECRET) is None
