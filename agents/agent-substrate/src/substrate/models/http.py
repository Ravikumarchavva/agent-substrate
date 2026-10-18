"""HTTP for the services the engine reaches by URL — standard library only.

An embedder, a reranker and a document server are all "a base URL, a token, JSON in, JSON out". This is that, in one place, so
the engine can talk to any of them without a client library: ``urllib`` on a worker thread (so it never blocks the event loop),
and a small, fixed way of turning what can go wrong into the engine's typed errors:

=============================  =========================================================================
connection refused, timeout    ``ServiceUnavailableError`` (transient: retry, or fall back to something local)
HTTP 5xx / 408                 ``ServiceUnavailableError``
HTTP 429                       ``RateLimitedError`` carrying the server's ``Retry-After``
HTTP 401 / 403                 ``AuthError``
HTTP 400 / 413 / 422           ``ContextLengthError`` when the body says the input was too long, else ``PermanentError``
any other 4xx, a redirect      ``PermanentError`` (a redirect is refused: a service answers where it was asked)
=============================  =========================================================================

Only ``http`` and ``https`` URLs are accepted — never ``file:`` or ``ftp:``.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit

from substrate.models.errors import _CONTEXT_OVERFLOW_MARKERS, retry_after_of
from substrate.types.errors import (
    AuthError,
    ContextLengthError,
    PermanentError,
    RateLimitedError,
    ServiceUnavailableError,
)

_MAX_RESPONSE_BYTES = 256 * 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # noqa: D401
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def join_url(base: str, path: str) -> str:
    """``base`` (a service's root URL) and ``path``, with exactly one ``/`` between them."""
    return base.rstrip("/") + "/" + path.lstrip("/")


def check_url(url: str) -> str:
    """``url`` if it is an ``http(s)`` URL with a host; ``ValueError`` otherwise."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(f"not an http(s) URL: {url!r}")
    return url


def _error_for(status: int, body: str, headers: Any, url: str) -> Exception:
    snippet = " ".join(body.split())[:300]
    message = f"{url} answered {status}" + (f": {snippet}" if snippet else "")
    if status in (408, 425) or status >= 500:
        return ServiceUnavailableError(message, url=url)
    if status == 429:
        retry_after = None
        try:
            retry_after = (
                float(headers.get("Retry-After"))
                if headers is not None and headers.get("Retry-After")
                else None
            )
        except ValueError:
            retry_after = None
        return RateLimitedError(message, retry_after=retry_after)
    if status in (401, 403):
        return AuthError(message)
    if status in (400, 413, 422) and any(
        marker in body.lower() for marker in _CONTEXT_OVERFLOW_MARKERS
    ):
        return ContextLengthError(message)
    return PermanentError(message)


def _call(
    method: str, url: str, data: bytes | None, headers: dict[str, str], timeout: float
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return response.status, response.read(_MAX_RESPONSE_BYTES)
    except urllib.error.HTTPError as exc:
        body = exc.read(64 * 1024).decode("utf-8", "replace")
        raise _error_for(exc.code, body, exc.headers, url) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        raise ServiceUnavailableError(
            f"{url} is unreachable: {getattr(exc, 'reason', exc)}", url=url
        ) from exc


async def request_json(
    method: str,
    url: str,
    *,
    json_body: Any = None,
    api_key: str = "",
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> Any:
    """Call ``url`` with an optional JSON body and return the decoded JSON of a successful answer.

    Raises the typed errors in the module docstring; an answer that is not JSON is a ``PermanentError`` (the server is not
    the service we think it is)."""
    check_url(url)
    send = {"Accept": "application/json", **(headers or {})}
    payload: bytes | None = None
    if json_body is not None:
        payload = json.dumps(json_body, separators=(",", ":")).encode("utf-8")
        send["Content-Type"] = "application/json"
    if api_key:
        send["Authorization"] = f"Bearer {api_key}"
    _status, raw = await asyncio.to_thread(_call, method, url, payload, send, timeout)
    try:
        return json.loads(raw) if raw else None
    except ValueError as exc:
        raise PermanentError(f"{url} did not answer with JSON") from exc


__all__ = ["check_url", "join_url", "request_json", "retry_after_of"]
