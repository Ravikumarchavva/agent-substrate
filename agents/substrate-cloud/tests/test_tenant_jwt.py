from __future__ import annotations

import jwt

from substrate.serving.shared.auth.jwt import create_access_token, verify_token


_SECRET = "a" * 32


def test_access_token_requires_a_real_tenant() -> None:
    token = jwt.encode({"sub": "user", "type": "access"}, _SECRET, algorithm="HS256")
    assert verify_token(token, _SECRET, expected_type="access") is None


def test_access_token_preserves_explicit_tenant() -> None:
    token, _ = create_access_token("user", _SECRET, tenant_id="project-id")
    claims = verify_token(token, _SECRET, expected_type="access")
    assert claims is not None
    assert claims.tenant_id == "project-id"
