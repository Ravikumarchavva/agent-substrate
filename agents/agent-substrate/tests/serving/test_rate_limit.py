"""Tests for the sliding-window HTTP rate limiter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from ravi.serving.shared.rate_limit import (
    _client_ip,
    _sliding_window_check,
    rate_limit,
    rate_limit_settings,
)


# ---------------------------------------------------------------------------
# _client_ip
# ---------------------------------------------------------------------------


def _make_request(host: str = "1.2.3.4", forwarded: str | None = None) -> Request:
    headers = []
    if forwarded:
        headers.append((b"x-forwarded-for", forwarded.encode()))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "query_string": b"",
        "client": (host, 9999),
    }
    return Request(scope)


def test_client_ip_direct():
    assert _client_ip(_make_request(host="10.0.0.1")) == "10.0.0.1"


def test_client_ip_forwarded_single():
    assert _client_ip(_make_request(forwarded="203.0.113.5")) == "203.0.113.5"


def test_client_ip_forwarded_chain():
    assert _client_ip(_make_request(forwarded="203.0.113.5, 10.0.0.1")) == "203.0.113.5"


def test_client_ip_forwarded_strips_whitespace():
    assert (
        _client_ip(_make_request(forwarded="  203.0.113.99  , 10.0.0.2"))
        == "203.0.113.99"
    )


# ---------------------------------------------------------------------------
# _sliding_window_check
# ---------------------------------------------------------------------------


def _make_redis_mock(card_result: int) -> MagicMock:
    """Return a fake Redis with a pipeline that reports `card_result` entries."""
    pipe = MagicMock()
    pipe.zremrangebyscore = MagicMock()
    pipe.zadd = MagicMock()
    pipe.zcard = MagicMock()
    pipe.expire = MagicMock()
    pipe.execute = AsyncMock(return_value=[0, 1, card_result, True])

    redis = MagicMock()
    redis.pipeline = MagicMock(return_value=pipe)
    return redis


async def test_sliding_window_first_request_returns_one():
    redis = _make_redis_mock(card_result=1)
    count, reset_in = await _sliding_window_check(
        redis, "rl:test", limit=5, window_seconds=60
    )
    assert count == 1
    assert reset_in == 60


async def test_sliding_window_count_grows():
    redis = _make_redis_mock(card_result=4)
    count, _ = await _sliding_window_check(redis, "rl:test", limit=5, window_seconds=60)
    assert count == 4


async def test_sliding_window_calls_pipeline_ops():
    redis = _make_redis_mock(card_result=1)
    pipe = redis.pipeline.return_value
    await _sliding_window_check(redis, "rl:mykey", limit=5, window_seconds=30)

    pipe.zremrangebyscore.assert_called_once()
    pipe.zadd.assert_called_once()
    pipe.zcard.assert_called_once()
    pipe.expire.assert_called_once()
    pipe.execute.assert_awaited_once()


# ---------------------------------------------------------------------------
# rate_limit dependency — integration via FastAPI test app
# ---------------------------------------------------------------------------


def _make_app(
    *, enabled: bool = True, authed_rpm: int = 10, anon_rpm: int = 2, redis=None
) -> FastAPI:
    app = FastAPI()
    app.state.rate_limit_settings = rate_limit_settings(
        enabled=enabled,
        authed_rpm=authed_rpm,
        anon_rpm=anon_rpm,
        window_seconds=60,
    )
    app.state.redis = redis

    @app.get("/check")
    async def check(_: None = Depends(rate_limit)):
        return {"ok": True}

    return app


def test_rate_limit_disabled_always_passes():
    app = _make_app(enabled=False, redis=None)
    with TestClient(app) as client:
        for _ in range(10):
            assert client.get("/check").status_code == 200


def test_rate_limit_no_redis_passes_through():
    """Missing Redis must not block requests — graceful degradation."""
    app = _make_app(anon_rpm=1, redis=None)
    with TestClient(app) as client:
        for _ in range(5):
            assert client.get("/check").status_code == 200


def test_rate_limit_anon_blocks_after_limit():
    """Anonymous callers get 429 once anon_rpm is exceeded."""
    call_count = 0

    def make_pipe():
        nonlocal call_count
        pipe = MagicMock()
        pipe.zremrangebyscore = MagicMock()
        pipe.zadd = MagicMock()
        pipe.zcard = MagicMock()
        pipe.expire = MagicMock()

        async def execute():
            nonlocal call_count
            call_count += 1
            return [0, 1, call_count, True]

        pipe.execute = execute
        return pipe

    redis = MagicMock()
    redis.pipeline = MagicMock(side_effect=make_pipe)

    app = _make_app(anon_rpm=2, redis=redis)
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/check").status_code == 200  # count=1, limit=2 → ok
        assert client.get("/check").status_code == 200  # count=2, limit=2 → ok
        r = client.get("/check")  # count=3, limit=2 → 429
        assert r.status_code == 429
        assert "Retry-After" in r.headers
        assert r.json()["detail"].startswith("Rate limit exceeded")


def test_rate_limit_429_has_correct_headers():
    call_count = 0

    def make_pipe():
        nonlocal call_count
        pipe = MagicMock()
        pipe.zremrangebyscore = MagicMock()
        pipe.zadd = MagicMock()
        pipe.zcard = MagicMock()
        pipe.expire = MagicMock()

        async def execute():
            nonlocal call_count
            call_count += 1
            return [0, 1, call_count, True]

        pipe.execute = execute
        return pipe

    redis = MagicMock()
    redis.pipeline = MagicMock(side_effect=make_pipe)
    app = _make_app(anon_rpm=1, redis=redis)

    with TestClient(app, raise_server_exceptions=False) as client:
        client.get("/check")  # consume the 1 allowed request
        r = client.get("/check")
        assert r.status_code == 429
        assert r.headers["X-RateLimit-Limit"] == "1"
        assert r.headers["X-RateLimit-Remaining"] == "0"
        assert "Retry-After" in r.headers


# ---------------------------------------------------------------------------
# rate_limit_settings helper
# ---------------------------------------------------------------------------


def test_rate_limit_settings_defaults():
    s = rate_limit_settings()
    assert s["enabled"] is True
    assert s["authed_rpm"] == 60
    assert s["anon_rpm"] == 5
    assert s["window_seconds"] == 60


def test_rate_limit_settings_custom():
    s = rate_limit_settings(
        enabled=False, authed_rpm=120, anon_rpm=10, window_seconds=3600
    )
    assert s["enabled"] is False
    assert s["authed_rpm"] == 120
    assert s["anon_rpm"] == 10
    assert s["window_seconds"] == 3600
