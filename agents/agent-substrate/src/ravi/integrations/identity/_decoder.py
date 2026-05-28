"""JWT → IdentityContext decoder.

Verifies an access token using the application secret and algorithm, then
maps the decoded :class:`AuthClaims` to the kernel identity model.

The kernel model has no concept of "user", "role", or "email" — those are
web-boundary concerns.  The bridge maps:

    AuthClaims.sub       → PrincipalId.name (stable external identity)
    AuthClaims.tenant_id → PrincipalId.tenant_id
    AuthClaims.workspace_id → PrincipalId.workspace_id
    AuthClaims.role      → PrincipalKind  (admin / platform_admin → SERVICE,
                                           end_user / user → HUMAN, else SERVICE)

The resulting :class:`IdentityContext` carries a :class:`PrincipalId` with a
**new** uid on every decode — it is the caller's responsibility to look up
or register the principal in a :class:`PrincipalStore` if persistence is
required.

Usage
-----
.. code-block:: python

    identity = decode_jwt_to_identity(token_str, secret="...", algorithm="HS256")
    envelope = envelope.replace(identity=identity)
"""

from __future__ import annotations

import uuid
from typing import Any

import jwt
from jwt import DecodeError, ExpiredSignatureError, InvalidTokenError

from ravi.kernel.runtime._identity import (
    IdentityContext,
    PrincipalId,
    PrincipalKind,
)

__all__ = [
    "JWTDecodeError",
    "decode_jwt_to_identity",
]

_ROLE_TO_KIND: dict[str, PrincipalKind] = {
    "platform_admin": PrincipalKind.SERVICE,
    "tenant_admin": PrincipalKind.SERVICE,
    "service": PrincipalKind.SERVICE,
    "agent": PrincipalKind.AGENT,
    "tool": PrincipalKind.TOOL,
    "workflow": PrincipalKind.WORKFLOW,
    "end_user": PrincipalKind.HUMAN,
    "user": PrincipalKind.HUMAN,
}


class JWTDecodeError(ValueError):
    """Raised when a token cannot be decoded or its claims are invalid."""

    def __init__(self, message: str, *, cause: BaseException | None = None) -> None:
        super().__init__(message)
        self.__cause__ = cause


def decode_jwt_to_identity(
    token: str,
    secret: str,
    *,
    algorithm: str = "HS256",
    expected_type: str | None = "access",
) -> IdentityContext:
    """Decode and verify ``token``; return the kernel :class:`IdentityContext`.

    Parameters
    ----------
    token:
        Raw JWT string (typically from the ``Authorization: Bearer …`` header).
    secret:
        HMAC secret used for signature verification.
    algorithm:
        JWT algorithm (default ``"HS256"``).
    expected_type:
        When non-None, assert that the ``type`` claim matches.  Pass ``None``
        to skip the type check (e.g. for service or agent tokens).

    Raises
    ------
    JWTDecodeError
        On any decode, expiry, or claim-shape error.
    """
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            secret,
            algorithms=[algorithm],
            options={"require": ["sub", "exp"]},
        )
    except ExpiredSignatureError as exc:
        raise JWTDecodeError("token has expired", cause=exc) from exc
    except DecodeError as exc:
        raise JWTDecodeError(f"token decode failed: {exc}", cause=exc) from exc
    except InvalidTokenError as exc:
        raise JWTDecodeError(f"invalid token: {exc}", cause=exc) from exc

    if expected_type is not None:
        token_type = claims.get("type", "")
        if token_type != expected_type:
            raise JWTDecodeError(
                f"expected token type {expected_type!r}, got {token_type!r}"
            )

    sub: str = claims.get("sub", "")
    if not sub:
        raise JWTDecodeError("missing 'sub' claim")

    role: str = claims.get("role", "end_user")
    kind = _ROLE_TO_KIND.get(role, PrincipalKind.HUMAN)
    tenant_id: str = claims.get("tenant_id", "default")
    workspace_id: str = claims.get("workspace_id", "default")
    uid: str = claims.get("uid", "") or uuid.uuid4().hex

    principal = PrincipalId(
        kind=kind,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        name=sub,
        uid=uid,
    )
    return IdentityContext(principal=principal)
