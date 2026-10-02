"""A real HTTP server on localhost that answers with a scripted handler — for the clients that speak HTTP through the standard library
(``substrate.models.http``) and so cannot be pointed at an in-process transport. The handler is the same ``httpx.Request -> httpx.Response``
function the suites already use for the vendor SDK clients."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

Handler = Callable[[httpx.Request], httpx.Response]


@contextmanager
def serve(handler: Handler) -> Iterator[str]:
    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # noqa: D102
            pass

        def _answer(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            request = httpx.Request(self.command, f"http://127.0.0.1{self.path}", headers=dict(self.headers), content=body)
            response = handler(request)
            payload = response.content
            self.send_response(response.status_code)
            self.send_header("Content-Type", response.headers.get("content-type", "application/json"))
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = _answer  # noqa: N815

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
