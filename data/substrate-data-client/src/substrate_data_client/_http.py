"""Transport shared by the sync and async clients: auth, trace context, retries and problem decoding."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import email.utils
import random
import re
import secrets
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import httpx

from substrate_data_client.auth import current_token, current_token_async
from substrate_data_client.errors import DataError, Unavailable, error_for

IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "PUT", "DELETE", "OPTIONS"})
TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")
_traceparent: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "substrate_data_traceparent", default=None
)


def has_h2() -> bool:
    try:
        import h2  # noqa: F401
    except ImportError:
        return False
    return True


# ------------------------------------------------------------------ trace context
@contextlib.contextmanager
def trace_context(traceparent: str) -> Iterator[None]:
    """Make every request in this block continue the given W3C `traceparent`."""
    token = _traceparent.set(traceparent)
    try:
        yield
    finally:
        _traceparent.reset(token)


def _otel_traceparent() -> str | None:
    try:
        from opentelemetry import trace
    except ImportError:
        return None
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None
    return f"00-{context.trace_id:032x}-{context.span_id:016x}-{int(context.trace_flags):02x}"


def new_traceparent(parent: str | None = None) -> str:
    """A `traceparent` for one outgoing request: same trace as the caller's, a fresh span id."""
    found = TRACEPARENT.match(parent or _traceparent.get() or _otel_traceparent() or "")
    trace_id, flags = (found.group(1), found.group(3)) if found else (secrets.token_hex(16), "01")
    if int(trace_id, 16) == 0:
        trace_id = secrets.token_hex(16)
    span_id = secrets.token_hex(8)
    if int(span_id, 16) == 0:
        span_id = "0000000000000001"
    return f"00-{trace_id}-{span_id}-{flags}"


# ------------------------------------------------------------------ retries
@dataclass(frozen=True)
class RetryPolicy:
    """Retry idempotent requests on 429/502/503/504 and connection failures, with jittered backoff.

    A `Retry-After` header is honoured (up to `max_delay`). Pass `retry=None` to a client to never retry."""

    max_attempts: int = 4
    backoff: float = 0.25
    max_delay: float = 10.0
    statuses: frozenset[int] = frozenset({429, 502, 503, 504})

    def delay(self, retries_so_far: int, retry_after: float | None) -> float:
        exponential = min(self.max_delay, self.backoff * 2**retries_so_far)
        wait = exponential * random.uniform(0.5, 1.0)
        if retry_after is not None:
            wait = max(wait, min(retry_after, self.max_delay))
        return wait


STORAGE_RETRY = RetryPolicy(statuses=frozenset({500, 502, 503, 504}))


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    when = email.utils.parsedate_to_datetime(value) if value else None
    return max(0.0, when.timestamp() - time.time()) if when else None


def problem_from(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
        if isinstance(body, dict):
            return body
    except ValueError:
        pass
    return {"detail": response.text[:500]}


def failure(response: httpx.Response) -> DataError:
    """The exception for an error response. The body must already be read."""
    problem = problem_from(response)
    problem.setdefault("status", response.status_code)
    return error_for(
        response.status_code,
        problem,
        retry_after=parse_retry_after(response.headers.get("retry-after")),
    )


def unreachable(exc: Exception) -> Unavailable:
    return Unavailable(
        503, {"type": "connection-failed", "detail": f"Cannot reach the service: {exc!r}"}
    )


def retryable(method: str, idempotent: bool | None) -> bool:
    return method.upper() in IDEMPOTENT_METHODS if idempotent is None else idempotent


# ------------------------------------------------------------------ cores
class SyncCore:
    """A pooled `httpx.Client` plus the request loop. One per client; thread-safe."""

    def __init__(
        self,
        *,
        base_url: str = "",
        token: Any = None,
        timeout: httpx.Timeout | float = 600,
        transport: httpx.BaseTransport | None = None,
        retry: RetryPolicy | None = RetryPolicy(),
        http2: bool | None = None,
        traceparent: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
        api: bool = True,
    ):
        self.token, self.retry, self.sleep, self.api = token, retry, sleep, api
        self.traceparent = traceparent
        if isinstance(timeout, int | float):
            timeout = httpx.Timeout(30, read=timeout)
        self.http = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            http2=has_h2() if http2 is None else http2,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=40),
            follow_redirects=False,
        )

    def close(self) -> None:
        self.http.close()

    @staticmethod
    def _transient(response: httpx.Response) -> bool:
        """A 429 that says the quota is spent (a daily budget) will not pass in seconds: do not wait."""
        if response.status_code != 429:
            return True
        response.read()
        return problem_from(response).get("type", "").rsplit("/", 1)[-1] != "quota-exceeded"

    def headers(self, extra: dict[str, str] | None) -> dict[str, str]:
        headers = dict(extra or {})
        if self.api:
            bearer = current_token(self.token)
            if bearer:
                headers["Authorization"] = "Bearer " + bearer
            headers["traceparent"] = new_traceparent(self.traceparent)
        return headers

    def send(
        self,
        method: str,
        url: str,
        *,
        idempotent: bool | None = None,
        stream: bool = False,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """The final response (retried when allowed). It may be an error response: callers `check`."""
        attempts = self.retry.max_attempts if self.retry and retryable(method, idempotent) else 1
        for attempt in range(1, attempts + 1):
            request = self.http.build_request(method, url, headers=self.headers(headers), **kwargs)
            try:
                response = self.http.send(request, stream=stream)
            except httpx.TransportError as exc:
                if attempt >= attempts:
                    raise unreachable(exc) from exc
                self.sleep(self.retry.delay(attempt - 1, None))  # type: ignore[union-attr]
                continue
            if (
                self.retry
                and attempt < attempts
                and response.status_code in self.retry.statuses
                and self._transient(response)
            ):
                wait = self.retry.delay(
                    attempt - 1, parse_retry_after(response.headers.get("retry-after"))
                )
                response.close()
                self.sleep(wait)
                continue
            return response
        raise AssertionError("unreachable")

    def check(self, response: httpx.Response) -> httpx.Response:
        if response.status_code >= 400:
            response.read()
            response.close()
            raise failure(response)
        return response

    def call(self, method: str, url: str, **kwargs: Any) -> Any:
        response = self.check(self.send(method, url, **kwargs))
        return response.json() if response.content else None


class AsyncCore:
    """The async twin of `SyncCore`: one pooled `httpx.AsyncClient`, safe to share across tasks."""

    def __init__(
        self,
        *,
        base_url: str = "",
        token: Any = None,
        timeout: httpx.Timeout | float = 600,
        transport: httpx.AsyncBaseTransport | None = None,
        retry: RetryPolicy | None = RetryPolicy(),
        http2: bool | None = None,
        traceparent: str | None = None,
        sleep: Callable[[float], Any] = asyncio.sleep,
        api: bool = True,
    ):
        self.token, self.retry, self.sleep, self.api = token, retry, sleep, api
        self.traceparent = traceparent
        if isinstance(timeout, int | float):
            timeout = httpx.Timeout(30, read=timeout)
        self.http = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            http2=has_h2() if http2 is None else http2,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=40),
            follow_redirects=False,
        )

    async def close(self) -> None:
        await self.http.aclose()

    @staticmethod
    async def _transient(response: httpx.Response) -> bool:
        if response.status_code != 429:
            return True
        await response.aread()
        return problem_from(response).get("type", "").rsplit("/", 1)[-1] != "quota-exceeded"

    async def headers(self, extra: dict[str, str] | None) -> dict[str, str]:
        headers = dict(extra or {})
        if self.api:
            bearer = await current_token_async(self.token)
            if bearer:
                headers["Authorization"] = "Bearer " + bearer
            headers["traceparent"] = new_traceparent(self.traceparent)
        return headers

    async def send(
        self,
        method: str,
        url: str,
        *,
        idempotent: bool | None = None,
        stream: bool = False,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        attempts = self.retry.max_attempts if self.retry and retryable(method, idempotent) else 1
        for attempt in range(1, attempts + 1):
            request = self.http.build_request(
                method, url, headers=await self.headers(headers), **kwargs
            )
            try:
                response = await self.http.send(request, stream=stream)
            except httpx.TransportError as exc:
                if attempt >= attempts:
                    raise unreachable(exc) from exc
                await self.sleep(self.retry.delay(attempt - 1, None))  # type: ignore[union-attr]
                continue
            if (
                self.retry
                and attempt < attempts
                and response.status_code in self.retry.statuses
                and await self._transient(response)
            ):
                wait = self.retry.delay(
                    attempt - 1, parse_retry_after(response.headers.get("retry-after"))
                )
                await response.aclose()
                await self.sleep(wait)
                continue
            return response
        raise AssertionError("unreachable")

    async def check(self, response: httpx.Response) -> httpx.Response:
        if response.status_code >= 400:
            await response.aread()
            await response.aclose()
            raise failure(response)
        return response

    async def call(self, method: str, url: str, **kwargs: Any) -> Any:
        response = await self.check(await self.send(method, url, **kwargs))
        return response.json() if response.content else None
