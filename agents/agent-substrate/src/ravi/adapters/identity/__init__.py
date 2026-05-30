"""Identity plane integrations — JWT decoding and principal persistence.

Bridges the web boundary (JWT tokens) into the kernel identity model
(:class:`ravi.kernel.runtime._identity.IdentityContext`).

Public surface
--------------
``decode_jwt_to_identity``
    Pure function: ``str → IdentityContext``.  Verify + decode a JWT access
    token and return a fully-populated kernel identity context.

``PrincipalStore``
    Protocol: register, lookup, and list :class:`PrincipalId` records backed
    by any persistent substrate.

``PostgresPrincipalStore``
    SQLAlchemy-async implementation of :class:`PrincipalStore`.
"""

from __future__ import annotations

from ravi.adapters.identity._decoder import (
    JWTDecodeError,
    decode_jwt_to_identity,
)
from ravi.adapters.identity._principal_store import (
    PostgresPrincipalStore,
    PrincipalNotFound,
    PrincipalRecord,
    PrincipalStore,
)

__all__ = [
    "JWTDecodeError",
    "decode_jwt_to_identity",
    "PostgresPrincipalStore",
    "PrincipalNotFound",
    "PrincipalRecord",
    "PrincipalStore",
]
