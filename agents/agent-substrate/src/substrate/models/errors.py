"""Turn a provider's failure into a typed kernel error.

The vendor SDKs already retry connection errors, 408/409/429 and 5xx inside a single
request. What reaches us after that is one of:

* a request that can never succeed — bad key, malformed request — which must fail at once
  instead of burning every run-level retry;
* a context window that was too small — *not* a generic 400: shrinking the prompt and
  trying again is the remedy, so it gets its own type;
* a refusal on content grounds, which retrying will not change;
* a rate limit, which says when to come back, so the delay is carried on the error;
* anything else, which is still transient and left as it is (the run retries with backoff).
"""

from __future__ import annotations

from collections.abc import Mapping

from substrate.types.errors import (
    AuthError,
    ContentFilterError,
    ContextLengthError,
    KernelError,
    PermanentError,
    RateLimitedError,
)

_PERMANENT_STATUS = frozenset({400, 401, 403, 404, 413, 422})

# What providers say, in words, when the prompt does not fit. Matched case-insensitively
# against the error text; OpenAI, Anthropic, Gemini and the OpenAI-compatible servers
# (vLLM, Ollama, LM Studio) between them use all of these.
_CONTEXT_OVERFLOW_MARKERS = (
    "context_length_exceeded",
    "context length",
    "context window",
    "maximum context",
    "prompt is too long",
    "too many tokens",
    "input token count",
    "exceeds the maximum number of tokens",
    "reduce the length",
    "request too large",
)
_CONTENT_FILTER_MARKERS = (
    "content_filter",
    "content management policy",
    "content policy",
    "responsible ai",
    "safety_ratings",
    "blocked by safety",
)


def _chain(exc: BaseException) -> list[BaseException]:
    """The exception and what it was raised from: clients wrap an SDK error in their own."""
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in chain and len(chain) < 5:
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def _status_of(chain: list[BaseException]) -> int | None:
    # openai/anthropic expose ``status_code``; google-genai exposes an int ``code``.
    for candidate in chain:
        for attr in ("status_code", "code"):
            value = getattr(candidate, attr, None)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
    return None


def _headers_of(chain: list[BaseException]) -> Mapping[str, str]:
    for candidate in chain:
        response = getattr(candidate, "response", None)
        headers = getattr(response, "headers", None)
        if headers is not None:
            return headers
    return {}


def retry_after_of(chain: list[BaseException]) -> float | None:
    """Seconds the provider asked us to wait, from ``Retry-After`` (seconds) or
    ``retry-after-ms``, or a ``retry_after`` attribute on the error itself."""
    for candidate in chain:
        value = getattr(candidate, "retry_after", None)
        if isinstance(value, (int, float)) and value >= 0:
            return float(value)
    headers = _headers_of(chain)
    for name, scale in (("retry-after-ms", 0.001), ("retry-after", 1.0)):
        raw = headers.get(name) if hasattr(headers, "get") else None
        if raw is not None:
            try:
                seconds = float(raw) * scale
            except (TypeError, ValueError):
                continue
            if seconds >= 0:
                return seconds
    return None


def _message_of(chain: list[BaseException]) -> str:
    return " ".join(str(e) for e in chain).lower()


def classify_llm_error(exc: Exception) -> Exception:
    """A typed ``KernelError`` for a provider failure that has a meaning of its own;
    *exc* itself when it is already one, or is transient and should be retried."""
    if isinstance(exc, KernelError):
        return exc
    chain = _chain(exc)
    status = _status_of(chain)
    text = _message_of(chain)

    if status == 429:
        return RateLimitedError(str(exc), retry_after=retry_after_of(chain))
    if status in (401, 403):
        return AuthError(str(exc))
    if status in (400, 413, 422) or status is None:
        if any(marker in text for marker in _CONTEXT_OVERFLOW_MARKERS):
            return ContextLengthError(str(exc))
        if any(marker in text for marker in _CONTENT_FILTER_MARKERS):
            return ContentFilterError(str(exc))
    if status in _PERMANENT_STATUS:
        return PermanentError(str(exc))
    return exc


__all__ = ["classify_llm_error", "retry_after_of"]
