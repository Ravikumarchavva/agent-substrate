"""Short constructors for the typed errors the data server raises, and redaction of what an error may show.

Every failure is a `loamz.data.storage.errors` class; `kernel/problems.py` turns it into an RFC 9457 document through
`HTTP_STATUS`. These helpers only spell the common messages once.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from loamz.data.server.kernel.settings import Settings
from loamz.data.storage import errors
from loamz.data.storage.errors import (
    ConflictError,
    ForbiddenError,
    InvalidRequestError,
    LoamzError,
    NotFoundError,
    ServiceUnavailableError,
)


class UploadIncompleteError(ConflictError):
    """The object store does not (yet) hold the whole upload: parts are missing or it is still being written."""


class StatementTimeoutError(InvalidRequestError):
    """A statement ran past its time limit and was stopped (narrow it, or raise `timeout_seconds`)."""


class TenantNotProvisionedError(ConflictError):
    """The tenant has no workspace yet (`POST /api/v1/tenants` has not run for it)."""


class InsufficientScopeError(ForbiddenError):
    """A delegated token lacks the scope this route declares."""

    def __init__(self, message: str, *, required: Sequence[str]) -> None:
        super().__init__(message)
        self.required = list(required)

    def extensions(self) -> dict[str, Any]:
        return {"required": self.required}


_RELAYED: dict[str, type[LoamzError]] = {
    cls.code: cls
    for cls in (
        InvalidRequestError,
        errors.ContextLengthError,
        errors.UnsupportedContentError,
        errors.UnauthenticatedError,
        ForbiddenError,
        NotFoundError,
        ConflictError,
        UploadIncompleteError,
        TenantNotProvisionedError,
        StatementTimeoutError,
        errors.PreconditionFailedError,
        errors.QuotaExceededError,
        errors.VectorSpaceError,
        errors.SchemaVersionError,
    )
}
_BY_STATUS: dict[int, type[LoamzError]] = {
    400: InvalidRequestError,
    401: errors.UnauthenticatedError,
    403: ForbiddenError,
    404: NotFoundError,
    409: ConflictError,
    412: errors.PreconditionFailedError,
    413: errors.QuotaExceededError,
}


def error_from_problem(status: int, problem: dict[str, Any], fallback: str) -> LoamzError:
    """The typed error for a problem another Loamz Data process answered with (the compute controller, a worker).

    Its `code` names the class; a failure behind it (5xx) means that service is unavailable to us."""
    detail = str(problem.get("detail") or fallback)
    if status >= 500:
        return ServiceUnavailableError(detail)
    if status == 429:
        return errors.RateLimitedError(detail, retry_after=problem.get("retry_after"))
    code = str(problem.get("type", "")).rsplit("/", 1)[-1]
    return (_RELAYED.get(code) or _BY_STATUS.get(status, InvalidRequestError))(detail)


def not_found(what: str) -> NotFoundError:
    return NotFoundError(f"{what} was not found")


def forbidden(detail: str) -> ForbiddenError:
    return ForbiddenError(detail)


def invalid(detail: str) -> InvalidRequestError:
    return InvalidRequestError(detail)


def conflict(detail: str) -> ConflictError:
    return ConflictError(detail)


def unavailable(detail: str) -> ServiceUnavailableError:
    return ServiceUnavailableError(detail)


def public_error(error: BaseException, settings: Settings) -> str:
    """Bound diagnostics and remove secrets and local paths before they are stored or shown."""
    message = str(error)
    private = [
        (settings.document_embedding_token.get_secret_value(), "[redacted]"),
        (settings.document_reader_token.get_secret_value(), "[redacted]"),
        (settings.controller_token.get_secret_value(), "[redacted]"),
        (settings.worker_token.get_secret_value(), "[redacted]"),
        (settings.s3_secret_access_key.get_secret_value(), "[redacted]"),
        (settings.s3_access_key_id.get_secret_value(), "[redacted]"),
        (str(settings.scratch.resolve()), "[temporary data]"),
        (str(settings.metadata.resolve().parent), "[state]"),
    ]
    for value, replacement in private:
        if value:
            message = message.replace(value, replacement)
    return message[:1200]
