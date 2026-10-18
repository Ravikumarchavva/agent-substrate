"""RFC 9457 problems from the service as exceptions, one class per kind of failure.

    try:
        client.files.upload("main.ws.files", "a.txt", b"hello", if_none_match=True)
    except PreconditionFailed:      # the file already exists
        ...
    except RateLimited as exc:      # retried automatically for idempotent calls first
        time.sleep(exc.retry_after or 1)

Every error keeps `status`, `code` (the problem `type` slug), `detail`, `problem` (the whole body) and the
`request_id` to quote to an operator.
"""

from __future__ import annotations

from typing import Any


class DataError(Exception):
    """An RFC 9457 problem returned by the service (or a failure reaching it)."""

    def __init__(
        self, status: int, problem: dict[str, Any], *, retry_after: float | None = None
    ) -> None:
        self.status = status
        self.problem = problem
        self.code = str(problem.get("type", "")).rsplit("/", 1)[-1]
        self.detail = str(problem.get("detail", ""))
        self.request_id = problem.get("request_id")
        self.retry_after = retry_after
        super().__init__(f"{status} {self.code}: {self.detail}")


class InvalidRequest(DataError):
    """400: the request is malformed (a bad path, a bad body)."""


class Unauthorized(DataError):
    """401: no token, or the token is expired or untrusted."""


class Forbidden(DataError):
    """403: the caller may not do this (grants, ownership, read-only token)."""


class InsufficientScope(Forbidden):
    """403 `insufficient-scope`: a delegated token lacks the scope this route needs (`required`)."""

    @property
    def required(self) -> list[str]:
        return list(self.problem.get("required", []))


class NotFound(DataError):
    """404."""


class Conflict(DataError):
    """409: the state does not allow this (already exists, incomplete upload, size mismatch)."""


class PreconditionFailed(DataError):
    """412: `If-Match` / `If-None-Match` did not hold; reload and decide again."""


class QuotaExceeded(DataError):
    """413 `quota-exceeded`: the volume's byte quota would be passed."""

    @property
    def used_bytes(self) -> int | None:
        return self.problem.get("used_bytes")

    @property
    def quota_bytes(self) -> int | None:
        return self.problem.get("quota_bytes")


class PayloadTooLarge(DataError):
    """413 other than a quota (a file, import or result over a size limit)."""


class RateLimited(DataError):
    """429: slow down; `retry_after` seconds when the server said."""


class Unavailable(DataError):
    """502, 503, 504, or the service could not be reached at all."""


class ResultTooLarge(DataError):
    """The result is bigger than this process should hold. Aggregate in SQL, or use `stream()`."""

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


def error_for(
    status: int, problem: dict[str, Any], *, retry_after: float | None = None
) -> DataError:
    """The most specific exception for a problem: the `type` slug first, then the status."""
    code = str(problem.get("type", "")).rsplit("/", 1)[-1]
    if code == "insufficient-scope":
        cls: type[DataError] = InsufficientScope
    elif code == "quota-exceeded":
        cls = QuotaExceeded
    elif code in {"precondition-failed", "precondition-required"} or status == 412:
        cls = PreconditionFailed
    elif status == 400:
        cls = InvalidRequest
    elif status == 401:
        cls = Unauthorized
    elif status == 403:
        cls = Forbidden
    elif status == 404:
        cls = NotFound
    elif status == 409:
        cls = Conflict
    elif status == 413:
        cls = PayloadTooLarge
    elif status == 429 or code == "rate-limited":
        cls = RateLimited
    elif status in {502, 503, 504}:
        cls = Unavailable
    else:
        cls = DataError
    return cls(status, problem, retry_after=retry_after)
