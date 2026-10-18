"""RFC 9457 problem details and secret redaction for every error a service returns."""

from __future__ import annotations

from typing import Any

from substrate_data.core.models import Settings

PROBLEM_BASE = "https://substrate.dev/problems/"


class Problem(Exception):
    """An error with a stable machine-readable type; rendered as application/problem+json."""

    def __init__(self, status: int, code: str, detail: str, **extensions: Any):
        super().__init__(detail)
        self.status, self.code, self.detail, self.extensions = status, code, detail, extensions

    def body(self, instance: str, request_id: str) -> dict[str, Any]:
        return {
            "type": PROBLEM_BASE + self.code,
            "title": self.code.replace("-", " ").capitalize(),
            "status": self.status,
            "detail": self.detail,
            "instance": instance,
            "request_id": request_id,
            **self.extensions,
        }


def not_found(what: str) -> Problem:
    return Problem(404, "not-found", f"{what} was not found")


def forbidden(detail: str) -> Problem:
    return Problem(403, "forbidden", detail)


def invalid(detail: str, **extensions: Any) -> Problem:
    return Problem(400, "invalid-request", detail, **extensions)


def conflict(detail: str, **extensions: Any) -> Problem:
    return Problem(409, "conflict", detail, **extensions)


def unavailable(detail: str) -> Problem:
    return Problem(503, "compute-unavailable", detail)


def public_error(error: Exception, settings: Settings) -> str:
    """Bound diagnostics and remove secrets and local paths before persistence or output."""
    message = str(error)
    private = [
        (settings.embedding_token.get_secret_value(), "[redacted]"),
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
