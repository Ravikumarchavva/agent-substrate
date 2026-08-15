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
    """Every existing token (no `extra` at all) must still decode cleanly —
    the new field must not become a required claim."""
    token, _ = create_access_token("user-1", SECRET)
    claims = verify_token(token, SECRET)
    assert claims is not None
    assert claims.daily_message_limit is None
    assert claims.tenant_id == "default"
