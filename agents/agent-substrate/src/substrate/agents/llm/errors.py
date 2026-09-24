"""Classify provider errors so the run-level retry policy only retries what
can succeed on a second try.

The vendor SDKs already retry connection errors, 408/409/429 and 5xx inside a
single request. What reaches us after that is either still-transient (retry
the whole run with backoff) or a request that can never succeed — bad key,
malformed request, context window exceeded — which must fail immediately
instead of burning every retry.
"""

from __future__ import annotations

from substrate.kernel.exceptions import KernelError, PermanentError

_PERMANENT_STATUS = frozenset({400, 401, 403, 404, 413, 422})


def _status_of(exc: BaseException) -> int | None:
    # openai/anthropic expose ``status_code``; google-genai exposes an int ``code``.
    for candidate in (exc, exc.__cause__):
        if candidate is None:
            continue
        for attr in ("status_code", "code"):
            value = getattr(candidate, attr, None)
            if isinstance(value, int):
                return value
    return None


def classify_llm_error(exc: Exception) -> Exception:
    """``PermanentError`` for a request that can never succeed; *exc* itself
    otherwise (and for anything already a kernel error)."""
    if isinstance(exc, KernelError):
        return exc
    if _status_of(exc) in _PERMANENT_STATUS:
        return PermanentError(str(exc))
    return exc


__all__ = ["classify_llm_error"]
