"""RFC 9457 problems from Loamz Data as exceptions, one class per problem `code`.

    try:
        client.files.upload("tenant_acme.workspace.files", "a.txt", b"hello", if_none_match=True)
    except PreconditionFailedError:     # the file already exists
        ...
    except RateLimitedError as exc:     # retried automatically for idempotent calls first
        time.sleep(exc.retry_after or 1)

The classes mirror the server's typed errors (`loamz.data.storage.errors`) by name and by `code`, the last segment
of the problem's `type` (`https://loamz.com/problems/not-found`), so the same failure has the same name on both sides
without the client depending on the server. Every error keeps `status`, `code`, `detail`, `problem` (the whole
body) and the `request_id` to quote to an operator.
"""

from __future__ import annotations

from typing import Any, ClassVar


class DataError(Exception):
    """A problem returned by the service, or a failure reaching it."""

    code = "error"
    """The class's problem code; an instance carries the code the problem actually had."""
    retryable: ClassVar[bool] = False

    def __init__(
        self, status: int, problem: dict[str, Any], *, retry_after: float | None = None
    ) -> None:
        self.status = status
        self.problem = problem
        self.code = code_of(problem) or type(self).code
        self.detail = str(problem.get("detail", ""))
        self.request_id = problem.get("request_id")
        self.retry_after = retry_after
        super().__init__(f"{status} {self.code}: {self.detail}")


class InvalidRequestError(DataError):
    code = "invalid-request"


class ContextLengthError(InvalidRequestError):
    code = "context-length"


class UnsupportedContentError(InvalidRequestError):
    code = "unsupported-content"


class UnauthenticatedError(DataError):
    """401: no token, or an expired or untrusted one."""

    code = "unauthenticated"


class ForbiddenError(DataError):
    """403: the caller may not do this (grants, ownership, a read-only token)."""

    code = "forbidden"


class InsufficientScopeError(ForbiddenError):
    """403: a delegated token lacks the scope the route needs (`required`)."""

    code = "insufficient-scope"

    @property
    def required(self) -> list[str]:
        return list(self.problem.get("required", []))


class NotFoundError(DataError):
    code = "not-found"


class ConflictError(DataError):
    """409: the state does not allow this (already exists, an incomplete upload, a size mismatch)."""

    code = "conflict"
    retryable = True


class ScopeViolationError(DataError):
    """409: the record id belongs to another owner."""

    code = "scope-violation"


class PreconditionFailedError(DataError):
    """412: `If-Match` / `If-None-Match` did not hold; reload and decide again."""

    code = "precondition-failed"


class QuotaExceededError(DataError):
    """413: a size or count limit (a volume's byte quota, a file or result too large)."""

    code = "quota-exceeded"

    @property
    def limit(self) -> int | None:
        return self.problem.get("limit")

    @property
    def used(self) -> int | None:
        return self.problem.get("used")


class VectorSpaceError(DataError):
    code = "vector-space"


class SchemaVersionError(DataError):
    code = "schema-version"


class RateLimitedError(DataError):
    """429: slow down; `retry_after` seconds when the server said."""

    code = "rate-limited"
    retryable = True


class ServiceUnavailableError(DataError):
    """502, 503, 504, or the service could not be reached at all."""

    code = "unavailable"
    retryable = True


class ResultTooLargeError(DataError):
    """The result is bigger than this process should hold. Aggregate in SQL, or use `stream()`."""

    code = "result-too-large"

    def __init__(self, limit: int):
        super().__init__(
            413,
            {
                "type": "result-too-large",
                "detail": (
                    f"The result exceeds {limit / 2**20:.0f} MiB. Aggregate or filter in SQL "
                    "(GROUP BY, WHERE, LIMIT), iterate stream() in batches, or raise max_bytes."
                ),
            },
        )


ERRORS: dict[str, type[DataError]] = {
    cls.code: cls
    for cls in (
        InvalidRequestError,
        ContextLengthError,
        UnsupportedContentError,
        UnauthenticatedError,
        ForbiddenError,
        InsufficientScopeError,
        NotFoundError,
        ConflictError,
        ScopeViolationError,
        PreconditionFailedError,
        QuotaExceededError,
        VectorSpaceError,
        SchemaVersionError,
        RateLimitedError,
        ServiceUnavailableError,
    )
}
"""Problem `code` -> class."""

_BY_STATUS: dict[int, type[DataError]] = {
    400: InvalidRequestError,
    401: UnauthenticatedError,
    403: ForbiddenError,
    404: NotFoundError,
    409: ConflictError,
    412: PreconditionFailedError,
    413: QuotaExceededError,
    422: InvalidRequestError,
    429: RateLimitedError,
    502: ServiceUnavailableError,
    503: ServiceUnavailableError,
    504: ServiceUnavailableError,
}


def code_of(problem: dict[str, Any]) -> str:
    return str(problem.get("type", "")).rsplit("/", 1)[-1]


def error_for(
    status: int, problem: dict[str, Any], *, retry_after: float | None = None
) -> DataError:
    """The class for a problem: its `code` first, then the HTTP status."""
    cls = ERRORS.get(code_of(problem)) or _BY_STATUS.get(status, DataError)
    return cls(status, problem, retry_after=retry_after)


__all__ = [
    "ERRORS",
    "ConflictError",
    "ContextLengthError",
    "DataError",
    "ForbiddenError",
    "InsufficientScopeError",
    "InvalidRequestError",
    "NotFoundError",
    "PreconditionFailedError",
    "QuotaExceededError",
    "RateLimitedError",
    "ResultTooLargeError",
    "ScopeViolationError",
    "SchemaVersionError",
    "ServiceUnavailableError",
    "UnauthenticatedError",
    "UnsupportedContentError",
    "VectorSpaceError",
    "error_for",
]
