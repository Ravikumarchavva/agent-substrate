"""substrate-data client errors -> the engine's typed errors, so no caller ever has to know the transport.

Reads hide existence: a key under another user's folder (``path-forbidden``) or in a volume the caller cannot see is
*not found*, exactly like a key that is not there (never an oracle for what exists). A write that is refused is a
``ScopeViolationError``; a token without the scope is an ``AuthError``; an unreachable or busy service is a transient
``ServiceUnavailableError`` / ``RateLimitedError`` the caller may retry.
"""

from __future__ import annotations

from typing import NoReturn

from substrate_data_client import (
    Conflict,
    DataError,
    Forbidden,
    InsufficientScope,
    InvalidRequest,
    NotFound,
    PayloadTooLarge,
    PreconditionFailed,
    QuotaExceeded,
    RateLimited,
    Unauthorized,
    Unavailable,
)

from substrate.stores.files import WorkspacePathError, WorkspaceQuotaExceededError
from substrate.types.errors import (
    AuthError,
    KernelError,
    ObjectNotFoundError,
    PermanentError,
    RateLimitedError,
    ScopeViolationError,
    ServiceUnavailableError,
)


class FileTooLargeError(PermanentError):
    """The service refused a file over its size limit."""


class FileConflictError(PermanentError):
    """The write does not fit the file's state: an upload that is incomplete or the wrong size, or a precondition (create-only,
    compare-and-swap) that no longer holds. Reload and decide again."""


def raise_read(exc: DataError, key: str) -> NoReturn:
    """Re-raise *exc* from a read of *key*."""
    if isinstance(exc, (NotFound, Forbidden)) and not isinstance(
        exc, InsufficientScope
    ):
        raise ObjectNotFoundError(key) from exc
    raise_for(exc, key)


def raise_for(exc: DataError, key: str, *, tenant_id: str = "") -> NoReturn:
    """Re-raise *exc* as the engine's error for *key*."""
    if isinstance(exc, QuotaExceeded):
        raise WorkspaceQuotaExceededError(
            tenant_id, exc.used_bytes or 0, exc.quota_bytes or 0
        ) from exc
    if isinstance(exc, NotFound):
        raise ObjectNotFoundError(key) from exc
    if isinstance(exc, InsufficientScope):
        raise AuthError(
            f"the run's token cannot use files ({', '.join(exc.required) or exc.detail})"
        ) from exc
    if isinstance(exc, Forbidden):
        raise ScopeViolationError(
            f"not allowed to change {key!r}", record_id=key
        ) from exc
    if isinstance(exc, Unauthorized):
        raise AuthError(
            f"substrate-data refused the run's token: {exc.detail}"
        ) from exc
    if isinstance(exc, InvalidRequest):
        raise WorkspacePathError(exc.detail or f"invalid key {key!r}") from exc
    if isinstance(exc, (Conflict, PreconditionFailed)):
        raise FileConflictError(
            exc.detail or f"{key!r} changed or is incomplete"
        ) from exc
    if isinstance(exc, PayloadTooLarge):
        raise FileTooLargeError(exc.detail or f"{key!r} is too large") from exc
    if isinstance(exc, RateLimited):
        raise RateLimitedError(
            exc.detail or "rate limited", retry_after=exc.retry_after
        ) from exc
    if isinstance(exc, Unavailable):
        raise ServiceUnavailableError(
            exc.detail or "substrate-data is unavailable"
        ) from exc
    if isinstance(exc, KernelError):  # pragma: no cover - already typed
        raise exc
    raise ServiceUnavailableError(f"substrate-data: {exc}") from exc
