"""``substrate.models.http`` — the engine's one way of talking to a service by URL, held to the typed errors it promises.

Real sockets, no mocks: a tiny stdlib HTTP server answers each path with a scripted status."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from substrate.models.http import check_url, join_url, request_json
from substrate.types import (
    AuthError,
    ContextLengthError,
    PermanentError,
    RateLimitedError,
    ServiceUnavailableError,
)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # silence
        pass

    def _send(self, status: int, body: bytes = b"{}", **headers: str) -> None:
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key.replace("_", "-"), value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def _dispatch(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        received = self.rfile.read(length) if length else b""
        path = self.path
        if path == "/echo":
            self._send(
                200,
                json.dumps(
                    {
                        "body": json.loads(received or b"null"),
                        "auth": self.headers.get("Authorization"),
                    }
                ).encode(),
            )
        elif path == "/limited":
            self._send(429, b"slow down", Retry_After="7")
        elif path == "/forbidden":
            self._send(403, b"no")
        elif path == "/too-long":
            self._send(
                400, b'{"error": "this model\'s maximum context length is 8192 tokens"}'
            )
        elif path == "/bad":
            self._send(400, b"nope")
        elif path == "/boom":
            self._send(503, b"overloaded")
        elif path == "/redirect":
            self._send(302, b"", Location="/echo")
        elif path == "/text":
            self._send(200, b"<html>not json</html>")
        else:
            self._send(404, b"missing")


@pytest.fixture
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    httpd.server_close()


async def test_json_round_trips_with_a_bearer_token(server) -> None:
    out = await request_json(
        "POST", join_url(server, "/echo"), json_body={"a": [1, 2]}, api_key="s3cret"
    )
    assert out == {"body": {"a": [1, 2]}, "auth": "Bearer s3cret"}


@pytest.mark.parametrize(
    ("path", "error"),
    [
        ("/forbidden", AuthError),
        ("/too-long", ContextLengthError),
        ("/bad", PermanentError),
        ("/missing-path", PermanentError),
        ("/redirect", PermanentError),
        ("/text", PermanentError),
    ],
)
async def test_what_goes_wrong_becomes_a_typed_error(server, path, error) -> None:
    with pytest.raises(error) as caught:
        await request_json("GET", server + path)
    assert not isinstance(caught.value, (ServiceUnavailableError, RateLimitedError))


async def test_rate_limiting_carries_the_servers_retry_after(server) -> None:
    with pytest.raises(RateLimitedError) as caught:
        await request_json("GET", server + "/limited")
    assert caught.value.retry_after == 7.0 and caught.value.retryable


async def test_a_5xx_and_an_unreachable_service_are_both_transient(server) -> None:
    with pytest.raises(ServiceUnavailableError) as five_hundred:
        await request_json("GET", server + "/boom")
    assert five_hundred.value.retryable and five_hundred.value.url.endswith("/boom")
    with pytest.raises(ServiceUnavailableError):
        await request_json("GET", "http://127.0.0.1:1/never", timeout=2)


async def test_a_timeout_is_transient(server) -> None:
    # a port that accepts and never answers
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    try:
        with pytest.raises(ServiceUnavailableError):
            await request_json(
                "GET", f"http://127.0.0.1:{sock.getsockname()[1]}/x", timeout=0.3
            )
    finally:
        sock.close()


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "gopher://x",
        "/just/a/path",
        "http://",
    ],
)
async def test_only_http_urls_are_ever_opened(url) -> None:
    with pytest.raises(ValueError):
        await request_json("GET", url)
    with pytest.raises(ValueError):
        check_url(url)


def test_join_url_has_exactly_one_slash() -> None:
    assert (
        join_url("http://h:8/", "/v1/x")
        == join_url("http://h:8", "v1/x")
        == "http://h:8/v1/x"
    )
