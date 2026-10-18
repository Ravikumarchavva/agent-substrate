"""The typed failures every Loamz package raises, and how each one reads over HTTP.

    LoamzError                 `code` (stable, machine-readable) and `retryable`
    ├── TransientError         trying again can help
    │   ├── ConflictError      someone else changed it first (optimistic concurrency, a busy resource)
    │   ├── RateLimitedError   carries `retry_after`
    │   └── ServiceUnavailableError   a service reached by URL is down, timed out or answered 5xx
    └── PermanentError         trying again cannot help
        ├── InvalidRequestError       the input breaks a rule
        │   ├── ContextLengthError    longer than a model takes
        │   └── UnsupportedContentError   content a component cannot take
        ├── NotFoundError             nothing there (also what another tenant's or user's object looks like)
        ├── ForbiddenError            known caller, not allowed
        ├── UnauthenticatedError      no or bad credentials
        ├── PreconditionFailedError   a conditional write whose condition did not hold
        ├── ScopeViolationError       a write tried to take over a record of another scope
        ├── QuotaExceededError        a size or count limit
        ├── VectorSpaceError          vectors from two embedders mixed in one collection
        └── SchemaVersionError        state written by a newer build

Domain code raises these; only the server edge turns them into responses, through `HTTP_STATUS` (one table for
every Loamz server), as RFC 9457 problem documents whose `type` ends in the error's `code`. Clients map the same codes
back to the same classes. Every error survives pickling (it may cross a process boundary in a worker pool).
"""

from __future__ import annotations

import re
from typing import Any, ClassVar


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower()


def _rebuild(cls: type, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    return cls(*args, **kwargs)


class _Reconstructible:
    """Remembers its constructor arguments so pickle and copy can rebuild it (keyword-only fields included)."""

    _ctor: tuple[tuple[Any, ...], dict[str, Any]]

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        instance = super().__new__(cls, *args)  # type: ignore[call-arg]
        instance._ctor = (args, kwargs)
        return instance

    def __reduce__(self) -> tuple[Any, ...]:
        args, kwargs = self._ctor
        return (_rebuild, (type(self), args, kwargs))


class LoamzError(_Reconstructible, Exception):
    """Base of every typed Loamz failure. `code` derives from the class name (`NotFoundError` -> `not-found`)."""

    retryable: ClassVar[bool] = False
    code: ClassVar[str] = "error"

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "code" not in cls.__dict__:
            cls.code = _snake(cls.__name__.removesuffix("Error"))

    def extensions(self) -> dict[str, Any]:
        """Extra, safe-to-show fields for the problem document (never secrets, never other tenants' data)."""
        return {}


class TransientError(LoamzError):
    retryable: ClassVar[bool] = True


class PermanentError(LoamzError):
    pass


class ConflictError(TransientError):
    pass


class RateLimitedError(TransientError):
    def __init__(self, message: str = "rate limited", *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after

    def extensions(self) -> dict[str, Any]:
        return {} if self.retry_after is None else {"retry_after": self.retry_after}


class ServiceUnavailableError(TransientError):
    code: ClassVar[str] = "unavailable"

    def __init__(self, message: str = "service unavailable", *, url: str | None = None) -> None:
        super().__init__(message)
        self.url = url  # for logs only: an internal address is never shown to a caller


class InvalidRequestError(PermanentError):
    pass


class ContextLengthError(InvalidRequestError):
    """An input longer than a model takes (an embedder's or a chat model's context window)."""

    def __init__(
        self, message: str = "context length exceeded", *, limit: int | None = None
    ) -> None:
        super().__init__(message)
        self.limit = limit


class UnsupportedContentError(InvalidRequestError):
    """Content a component cannot take (an image for a text-only embedder): refused, never silently dropped."""


class NotFoundError(PermanentError):
    pass


class ForbiddenError(PermanentError):
    pass


class UnauthenticatedError(PermanentError):
    pass


class PreconditionFailedError(PermanentError):
    """A conditional write (`If-Match`, `If-None-Match`) whose condition did not hold: reload and decide again."""


class ScopeViolationError(PermanentError):
    """Record ids are chosen by callers, so an id colliding with another scope's record is refused, never overwritten."""

    def __init__(self, message: str, *, record_id: str) -> None:
        super().__init__(message)
        self.record_id = record_id


class QuotaExceededError(PermanentError):
    def __init__(self, message: str, *, limit: int | None = None, used: int | None = None) -> None:
        super().__init__(message)
        self.limit, self.used = limit, used

    def extensions(self) -> dict[str, Any]:
        return {k: v for k, v in (("limit", self.limit), ("used", self.used)) if v is not None}


class VectorSpaceError(PermanentError):
    def __init__(self, message: str, *, collection: str | None = None) -> None:
        super().__init__(message)
        self.collection = collection


class SchemaVersionError(PermanentError):
    pass


HTTP_STATUS: dict[type[LoamzError], int] = {
    InvalidRequestError: 400,
    UnauthenticatedError: 401,
    ForbiddenError: 403,
    NotFoundError: 404,
    ConflictError: 409,
    PreconditionFailedError: 412,
    ScopeViolationError: 409,
    QuotaExceededError: 413,
    VectorSpaceError: 422,
    RateLimitedError: 429,
    ServiceUnavailableError: 503,
}
"""Status per error class; a subclass takes its nearest listed ancestor's. Anything unlisted is a 500."""


def http_status(error: BaseException) -> int:
    for cls in type(error).__mro__:
        if cls in HTTP_STATUS:
            return HTTP_STATUS[cls]  # type: ignore[index]
    return 500


__all__ = [
    "HTTP_STATUS",
    "ConflictError",
    "ContextLengthError",
    "ForbiddenError",
    "InvalidRequestError",
    "LoamzError",
    "NotFoundError",
    "PermanentError",
    "PreconditionFailedError",
    "QuotaExceededError",
    "RateLimitedError",
    "SchemaVersionError",
    "ScopeViolationError",
    "ServiceUnavailableError",
    "TransientError",
    "UnauthenticatedError",
    "UnsupportedContentError",
    "VectorSpaceError",
    "http_status",
]
