"""Errors, retries, token sources and trace context, against httpx.MockTransport (no server)."""

from __future__ import annotations

import asyncio
import re
import subprocess
import sys
import types

import httpx
import pytest

import loamz.data as sd
from loamz.data import (
    AsyncDataClient,
    ConflictError,
    DataClient,
    DataError,
    ForbiddenError,
    InsufficientScopeError,
    InvalidRequestError,
    NotFoundError,
    PreconditionFailedError,
    QuotaExceededError,
    RateLimitedError,
    RetryPolicy,
    ServiceUnavailableError,
    UnauthenticatedError,
    trace_context,
)
from loamz.data.auth import ClientCredentials


def problem(status: int, slug: str, detail: str = "nope", **extra):
    body = {
        "type": f"https://loamz.com/problems/{slug}",
        "status": status,
        "detail": detail,
        "request_id": "req-1",
        **extra,
    }
    return httpx.Response(status, json=body, headers={"content-type": "application/problem+json"})


class Recorder:
    """A MockTransport handler that plays back canned responses and remembers the requests."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, Exception):
            raise response
        return response


def client(*responses, **options):
    recorder = Recorder(*responses)
    sleeps: list[float] = []
    options.setdefault("token", "tok")
    api = DataClient(
        "http://data.test",
        transport=httpx.MockTransport(recorder),
        sleep=sleeps.append,
        **options,
    )
    return api, recorder, sleeps


# ------------------------------------------------------------------ errors
@pytest.mark.parametrize(
    "status, slug, expected",
    [
        (400, "invalid-path", InvalidRequestError),
        (401, "unauthorized", UnauthenticatedError),
        (403, "forbidden", ForbiddenError),
        (403, "path-forbidden", ForbiddenError),
        (403, "token-read-only", ForbiddenError),
        (403, "insufficient-scope", InsufficientScopeError),
        (404, "not-found", NotFoundError),
        (409, "conflict", ConflictError),
        (409, "upload-incomplete", ConflictError),
        (412, "precondition-failed", PreconditionFailedError),
        (413, "quota-exceeded", QuotaExceededError),
        (429, "rate-limited", RateLimitedError),
        (503, "storage-unavailable", ServiceUnavailableError),
        (502, "result-incomplete", ServiceUnavailableError),
        (500, "internal-error", DataError),
    ],
)
def test_problem_types_map_to_exception_classes(status, slug, expected):
    api, _, _ = client(problem(status, slug), retry=None)
    with pytest.raises(DataError) as caught:
        api.request("POST", "/anything")
    error = caught.value
    assert type(error) is expected
    assert error.status == status and error.code == slug and error.request_id == "req-1"
    assert isinstance(error, DataError) and str(status) in str(error)


def test_error_details_survive_on_the_exception():
    api, _, _ = client(problem(413, "quota-exceeded", used=600, limit=1000), retry=None)
    with pytest.raises(QuotaExceededError) as quota:
        api.request("GET", "/x")
    assert (quota.value.used, quota.value.limit) == (600, 1000)
    api, _, _ = client(problem(403, "insufficient-scope", required=["files:write"]), retry=None)
    with pytest.raises(InsufficientScopeError) as scope:
        api.request("GET", "/x")
    assert scope.value.required == ["files:write"] and isinstance(scope.value, ForbiddenError)


def test_a_non_json_error_body_still_becomes_a_data_error():
    api, _, _ = client(httpx.Response(500, text="<html>boom</html>"), retry=None)
    with pytest.raises(DataError) as caught:
        api.request("GET", "/x")
    assert caught.value.status == 500 and "boom" in caught.value.detail


# ------------------------------------------------------------------ retries
def test_idempotent_requests_are_retried_with_backoff_then_succeed():
    api, recorder, sleeps = client(
        problem(503, "not-ready"), problem(502, "bad-gateway"), httpx.Response(200, json={"ok": 1})
    )
    assert api.request("GET", "/catalogs") == {"ok": 1}
    assert len(recorder.requests) == 3 and len(sleeps) == 2
    assert 0 < sleeps[0] <= 0.25 and sleeps[0] <= sleeps[1] * 2 + 0.01


def test_retry_after_is_honoured():
    api, _, sleeps = client(
        httpx.Response(429, json={"type": "rate-limited"}, headers={"Retry-After": "3"}),
        httpx.Response(200, json={}),
    )
    api.request("GET", "/x")
    assert sleeps == [3.0]


def test_retries_stop_after_the_last_attempt_and_report_the_server_wait():
    api, recorder, sleeps = client(
        httpx.Response(
            429, json={"type": "rate-limited", "detail": "slow"}, headers={"Retry-After": "2"}
        ),
        retry=RetryPolicy(max_attempts=3),
    )
    with pytest.raises(RateLimitedError) as limited:
        api.request("GET", "/x")
    assert limited.value.retry_after == 2.0
    assert len(recorder.requests) == 3 and len(sleeps) == 2


def test_non_idempotent_posts_are_never_retried():
    api, recorder, sleeps = client(
        problem(503, "storage-unavailable"), httpx.Response(200, json={})
    )
    with pytest.raises(ServiceUnavailableError):
        api.request("POST", "/volumes/v/files:initiate-upload", json={})
    assert len(recorder.requests) == 1 and sleeps == []
    api, recorder, _ = client(problem(503, "x"), httpx.Response(200, json={"done": True}))
    assert api.request("POST", "/p", json={}, idempotent=True) == {"done": True}
    assert len(recorder.requests) == 2


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_put_and_delete_are_idempotent_by_method(method):
    api, recorder, _ = client(problem(504, "timeout"), httpx.Response(204))
    api.request(method, "/x")
    assert len(recorder.requests) == 2


def test_client_errors_are_not_retried():
    api, recorder, _ = client(problem(404, "not-found"), httpx.Response(200, json={}))
    with pytest.raises(NotFoundError):
        api.request("GET", "/x")
    assert len(recorder.requests) == 1


def test_a_dead_connection_is_retried_then_reported_as_unavailable():
    api, recorder, sleeps = client(httpx.ConnectError("refused"), retry=RetryPolicy(max_attempts=3))
    with pytest.raises(ServiceUnavailableError) as down:
        api.request("GET", "/x")
    assert down.value.code == "connection-failed" and len(recorder.requests) == 3
    assert len(sleeps) == 2
    api, recorder, _ = client(httpx.ConnectError("refused"), httpx.Response(200, json={"a": 1}))
    assert api.request("GET", "/x") == {"a": 1}


def test_retry_can_be_turned_off():
    api, recorder, _ = client(problem(503, "x"), httpx.Response(200, json={}), retry=None)
    with pytest.raises(ServiceUnavailableError):
        api.request("GET", "/x")
    assert len(recorder.requests) == 1


def test_the_async_client_retries_the_same_way():
    recorder = Recorder(problem(503, "x"), httpx.Response(200, json={"ok": True}))
    sleeps: list[float] = []

    async def pause(seconds: float) -> None:
        sleeps.append(seconds)

    async def run():
        async with AsyncDataClient(
            "http://data.test", transport=httpx.MockTransport(recorder), sleep=pause
        ) as api:
            return await api.request("GET", "/x")

    assert asyncio.run(run()) == {"ok": True} and len(sleeps) == 1


# ------------------------------------------------------------------ token sources
def bearer(request: httpx.Request) -> str:
    return request.headers.get("authorization", "")


def test_a_string_token_and_a_function_token_are_sent_as_bearer():
    api, recorder, _ = client(httpx.Response(200, json={}), token="fixed")
    api.request("GET", "/x")
    assert bearer(recorder.requests[0]) == "Bearer fixed"
    counter = iter(range(10))
    api, recorder, _ = client(httpx.Response(200, json={}), token=lambda: f"t{next(counter)}")
    api.request("GET", "/x")
    api.request("GET", "/x")
    assert [bearer(r) for r in recorder.requests] == ["Bearer t0", "Bearer t1"]  # asked per request
    api, recorder, _ = client(httpx.Response(200, json={}), token=None)
    api.request("GET", "/x")
    assert "authorization" not in recorder.requests[0].headers


def test_an_async_token_function_needs_the_async_client():
    async def token() -> str:
        return "from-async"

    recorder = Recorder(httpx.Response(200, json={}))

    async def run():
        async with AsyncDataClient(
            "http://data.test", token=token, transport=httpx.MockTransport(recorder)
        ) as api:
            await api.request("GET", "/x")
        # and a plain function works there too
        async with AsyncDataClient(
            "http://data.test", token=lambda: "plain", transport=httpx.MockTransport(recorder)
        ) as api:
            await api.request("GET", "/x")

    asyncio.run(run())
    assert [bearer(r) for r in recorder.requests] == ["Bearer from-async", "Bearer plain"]
    sync_api, _, _ = client(httpx.Response(200, json={}), token=token)
    with pytest.raises(TypeError, match="AsyncDataClient"):
        sync_api.request("GET", "/x")


def test_client_credentials_fetch_once_and_cache_until_near_expiry(monkeypatch):
    issued = []

    def fake_post(url, data, timeout):
        issued.append((url, dict(data)))
        return httpx.Response(
            200,
            json={"access_token": f"jwt-{len(issued)}", "expires_in": 300},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("loamz.data.auth.httpx", types.SimpleNamespace(post=fake_post))
    credentials = ClientCredentials("https://idp/realms/x/", "svc", "s3cret", scope="data:read")
    api, recorder, _ = client(httpx.Response(200, json={}), token=credentials)
    api.request("GET", "/x")
    api.request("GET", "/x")
    assert len(issued) == 1 and issued[0][0] == "https://idp/realms/x/protocol/openid-connect/token"
    assert (
        issued[0][1]["grant_type"] == "client_credentials" and issued[0][1]["scope"] == "data:read"
    )
    assert [bearer(r) for r in recorder.requests] == ["Bearer jwt-1", "Bearer jwt-1"]


def test_client_credentials_work_with_the_async_client(monkeypatch):
    seen = []

    class FakeHttp:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, data):
            seen.append(url)
            return httpx.Response(
                200,
                json={"access_token": "async-jwt", "expires_in": 300},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr("loamz.data.auth.httpx", types.SimpleNamespace(AsyncClient=FakeHttp))
    recorder = Recorder(httpx.Response(200, json={}))

    async def run():
        async with AsyncDataClient(
            "http://data.test",
            token=ClientCredentials("https://idp/realms/x", "svc", "s"),
            transport=httpx.MockTransport(recorder),
        ) as api:
            await api.request("GET", "/x")
            await api.request("GET", "/x")

    asyncio.run(run())
    assert len(seen) == 1 and bearer(recorder.requests[1]) == "Bearer async-jwt"


# ------------------------------------------------------------------ trace context
TRACEPARENT = re.compile(r"^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$")


def test_every_request_carries_a_valid_traceparent_with_a_fresh_span():
    api, recorder, _ = client(httpx.Response(200, json={}))
    api.request("GET", "/a")
    api.request("GET", "/b")
    first, second = (r.headers["traceparent"] for r in recorder.requests)
    assert TRACEPARENT.match(first) and TRACEPARENT.match(second)
    assert first.split("-")[1] != second.split("-")[1]  # separate traces by default
    assert first != second


def test_requests_continue_the_callers_trace():
    parent = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    api, recorder, _ = client(httpx.Response(200, json={}))
    with trace_context(parent):
        api.request("GET", "/a")
        api.request("GET", "/b")
    api.request("GET", "/c")
    inside = [r.headers["traceparent"].split("-") for r in recorder.requests[:2]]
    assert [p[1] for p in inside] == ["0af7651916cd43dd8448eb211c80319c"] * 2
    assert inside[0][2] != inside[1][2] and inside[0][2] != "b7ad6b7169203331"  # new span ids
    assert (
        recorder.requests[2].headers["traceparent"].split("-")[1]
        != "0af7651916cd43dd8448eb211c80319c"
    )
    pinned, rec2, _ = client(httpx.Response(200, json={}), traceparent=parent)
    pinned.request("GET", "/a")
    assert (
        rec2.requests[0].headers["traceparent"].split("-")[1] == "0af7651916cd43dd8448eb211c80319c"
    )


def test_retried_requests_share_a_trace_and_storage_calls_carry_no_api_headers():
    api, recorder, _ = client(problem(503, "x"), httpx.Response(200, json={}))
    with trace_context("00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"):
        api.request("GET", "/a")
    assert len({r.headers["traceparent"].split("-")[1] for r in recorder.requests}) == 1
    storage = Recorder(httpx.Response(200, content=b"x"))
    plain = DataClient(
        "http://data.test",
        token="secret",
        transport=httpx.MockTransport(Recorder(httpx.Response(200, json={}))),
        storage_transport=httpx.MockTransport(storage),
    )
    plain.files.put_to_url("http://s3.test/obj?sig=1", b"x", {"Content-Type": "text/plain"})
    sent = storage.requests[0].headers
    assert (
        "authorization" not in sent
        and "traceparent" not in sent
        and sent["content-type"] == "text/plain"
    )


# ------------------------------------------------------------------ optional extras
def test_the_core_import_does_not_load_arrow_or_polars():
    code = (
        "import sys, loamz.data as sd\n"
        "assert 'pyarrow' not in sys.modules and 'polars' not in sys.modules, sorted(sys.modules)\n"
        "assert not any(m.startswith(('numpy', 'pandas')) for m in sys.modules)\n"
        "print('ok')"
    )
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert done.stdout.strip() == "ok", done.stderr


def test_arrow_helpers_explain_the_missing_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyarrow", None)
    monkeypatch.setitem(sys.modules, "polars", None)
    api, recorder, _ = client(
        httpx.Response(
            200,
            json={
                "columns": [{"name": "n", "type": "int64"}],
                "rows": [[1], [2]],
                "truncated": False,
            },
        )
    )
    for call in (
        lambda: api.sql("SELECT 1"),
        lambda: api.sql.arrow("SELECT 1"),
        lambda: list(api.sql.stream("SELECT 1")),
        lambda: api.arrow("SELECT 1"),
    ):
        with pytest.raises(ImportError, match=r"loamz-data-client\[arrow\]"):
            call()
    assert recorder.requests == []  # nothing was sent for a result that could not be read
    result = api.sql.rows("SELECT n FROM t")  # JSON works with the core alone
    assert result.names == ["n"] and result.records() == [{"n": 1}, {"n": 2}] and len(result) == 2
    monkeypatch.setattr(sd, "_default", api)
    with pytest.raises(ImportError, match=r"\[arrow\]"):
        sd.sql("SELECT 1")
    with pytest.raises(ImportError, match=r"\[arrow\]"):
        sd.arrow("SELECT 1")
    with pytest.raises(ImportError, match=r"\[arrow\]"):
        sd.stream("SELECT 1")
    assert sd.rows("SELECT n FROM t").rows == [[1], [2]]
