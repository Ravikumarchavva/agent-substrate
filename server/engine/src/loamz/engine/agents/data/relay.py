"""`DataGateway`: a hardened, allow-listed HTTP forwarder on a Unix domain socket.

The sandbox has no network interface. It is mounted a single Unix domain socket connected to
this gateway, which validates TLS and forwards only allow-listed query and catalog calls into the
data service.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import socket
import ssl
import stat
from http.cookiejar import DefaultCookiePolicy
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import uvicorn

logger = logging.getLogger(__name__)

UNIX_PATH_LIMIT = 100
LOOPBACK = {"localhost", "127.0.0.1", "::1"}
MAX_REQUEST_BYTES = 1 << 20  # 1 MiB max

_SEGMENT = r"[A-Za-z0-9_.$-]{1,128}"
ALLOWED = (
    (
        "GET",
        re.compile(
            rf"^/api/v1/(search|catalogs(/{_SEGMENT}(/schemas(/{_SEGMENT}"
            rf"(/(tables|views)(/{_SEGMENT})?)?)?)?)?)$"
        ),
    ),
    ("POST", re.compile(r"^/api/v1/statements$")),
    ("POST", re.compile(rf"^/api/v1/statements/{_SEGMENT}:cancel$")),
)

FORWARDED_REQUEST_HEADERS = {
    "authorization",
    "content-type",
    "accept",
    "traceparent",
    "tracestate",
    "x-request-id",
}
FORWARDED_RESPONSE_HEADERS = {
    "content-type",
    "x-request-id",
    "retry-after",
    "etag",
    "location",
}


def _problem(status: int, kind: str, detail: str) -> tuple[int, bytes]:
    body = {
        "type": f"https://loamz.com/problems/{kind}",
        "title": kind.replace("-", " "),
        "status": status,
        "detail": detail,
    }
    return status, json.dumps(body).encode()


class DataGateway:
    """The sandbox's egress relay on a Unix domain socket."""

    def __init__(
        self,
        directory: Path,
        upstream_url: str,
        *,
        ca_file: str | None = None,
        max_connections: int = 64,
        connect_seconds: float = 10,
        read_seconds: float = 600,
        insecure_hosts: tuple[str, ...] = (),
    ) -> None:
        parts = urlsplit(upstream_url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("The data service URL must be http(s)://host[:port]")
        if parts.scheme == "http" and parts.hostname not in LOOPBACK | set(insecure_hosts):
            raise ValueError("Plain http is only allowed to localhost and insecure_hosts")

        self.upstream = upstream_url.rstrip("/")
        self.directory = directory
        self.socket_path = directory / "api.sock"
        if len(str(self.socket_path)) > UNIX_PATH_LIMIT:
            raise ValueError(f"Gateway path is too long for a Unix socket: {self.socket_path}")

        context: ssl.SSLContext | bool = False
        if parts.scheme == "https":
            context = ssl.create_default_context(cafile=ca_file or None)
            context.minimum_version = ssl.TLSVersion.TLSv1_2

        self.client = httpx.AsyncClient(
            verify=context if parts.scheme == "https" else True,
            timeout=httpx.Timeout(connect=connect_seconds, read=read_seconds, write=30, pool=5),
            limits=httpx.Limits(max_connections=max_connections, max_keepalive_connections=16),
            follow_redirects=False,
            trust_env=False,
        )
        self.client.cookies.jar.set_policy(DefaultCookiePolicy(allowed_domains=[]))
        self.slots = asyncio.Semaphore(max_connections)
        self.server: uvicorn.Server | None = None
        self.task: asyncio.Task[None] | None = None
        self.start_lock = asyncio.Lock()

    async def ensure_started(self) -> None:
        async with self.start_lock:
            if self.server is not None:
                return
            self.directory.mkdir(parents=True, exist_ok=True)
            self.directory.chmod(0o700)
            self.socket_path.unlink(missing_ok=True)
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(self.socket_path))
            self.socket_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            listener.listen(128)

            async def app(scope, receive, send):  # type: ignore[no-untyped-def]
                await self.forward(scope, receive, send)

            config = uvicorn.Config(
                app,
                lifespan="off",
                log_level="warning",
                access_log=False,
                server_header=False,
            )
            self.server = uvicorn.Server(config)
            self.task = asyncio.create_task(self.server.serve(sockets=[listener]))
            while not self.server.started and not self.task.done():
                await asyncio.sleep(0.01)

    async def stop(self) -> None:
        async with self.start_lock:
            if self.server is not None:
                self.server.should_exit = True
                if self.task is not None:
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await asyncio.wait_for(self.task, 5)
                self.server, self.task = None, None
            self.socket_path.unlink(missing_ok=True)
        await self.client.aclose()

    async def forward(self, scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        if scope["type"] != "http":
            return
        method, path = scope["method"], scope["path"]
        dotted = {".", ".."} & set(path.split("/"))
        if dotted or not any(m == method and rx.match(path) for m, rx in ALLOWED):
            await self._reply(
                send,
                *_problem(
                    403, "not-allowed-from-sandbox", "This request is not available from a sandbox"
                ),
            )
            return

        query = scope.get("query_string", b"")
        if len(query) > 2048:
            await self._reply(send, *_problem(414, "uri-too-long", "Query string too long"))
            return

        body = b""
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return
            body += message.get("body", b"")
            if len(body) > MAX_REQUEST_BYTES:
                await self._reply(
                    send, *_problem(413, "request-too-large", "Request body too large")
                )
                return
            if not message.get("more_body"):
                break

        headers = {
            k.decode("latin-1"): v.decode("latin-1")
            for k, v in scope["headers"]
            if k.decode("latin-1").lower() in FORWARDED_REQUEST_HEADERS
        }
        headers["accept-encoding"] = "identity"

        async with self.slots:
            url = self.upstream + path + ("?" + query.decode("latin-1") if query else "")
            try:
                request = self.client.build_request(
                    method, url, headers=headers, content=body or None
                )
                response = await self.client.send(request, stream=True)
            except httpx.TimeoutException:
                await self._reply(
                    send,
                    *_problem(504, "upstream-timeout", "The data service did not answer in time"),
                )
                return
            except Exception as exc:  # noqa: BLE001
                await self._reply(
                    send, *_problem(502, "upstream-unavailable", f"Data service error: {exc}")
                )
                return

            res_headers = [
                (k.encode("latin-1"), v.encode("latin-1"))
                for k, v in response.headers.items()
                if k.lower() in FORWARDED_RESPONSE_HEADERS
            ]
            await send(
                {
                    "type": "http.response.start",
                    "status": response.status_code,
                    "headers": res_headers,
                }
            )
            async for chunk in response.aiter_raw():
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def _reply(self, send, status: int, body: bytes) -> None:  # type: ignore[no-untyped-def]
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/problem+json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})
