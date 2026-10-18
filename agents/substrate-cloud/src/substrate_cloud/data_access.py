"""Governed warehouse data for the code interpreter, when data and agents run on different machines.

The data service is a remote HTTPS service. Nothing here assumes it shares a host, a disk or a
network with this process. Four small parts, composed by ``factory.py``:

* :class:`DataTokenMinter` signs a short-lived, read-only token for one run: the user, their tenant
  and the acting agent, with an Ed25519 key only this platform holds. Its public keys (current and,
  during a rotation, the previous one) are published at ``/.well-known/data-token-jwks.json`` and the
  data service fetches them over HTTPS; no key or file is ever copied between machines.
* :class:`DataGateway` is the only thing a sandbox can reach. The jail has no network, so it gets one
  Unix socket, which exists on *this* host only. The gateway forwards a short allow-list of read
  requests to the data service over verified TLS (1.2 or newer; optionally mutual TLS) with a pooled
  connection, correct ``Host``/SNI and no redirects. It is the single egress point: it never forwards
  a path outside the allow-list, so a compromised sandbox cannot use it to reach anything else.
* :class:`PlatformDataAccess` ties them together behind the library's ``DataAccess`` seam.
"""

from __future__ import annotations

import asyncio
import contextlib
from http.cookiejar import DefaultCookiePolicy
import hashlib
import json
import logging
import os
import re
import socket
import ssl
import stat
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import jwt
import uvicorn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from substrate.integrations.tools.code_interpreter.code_interpreter.runtimes.base import (
    DataMount,
)
from substrate.types import RunScope

logger = logging.getLogger(__name__)

UNIX_PATH_LIMIT = 100  # sun_path is 108 bytes on Linux; leave room
LOOPBACK = {"localhost", "127.0.0.1", "::1"}
JWKS_PATH = "/.well-known/data-token-jwks.json"
MAX_TOKEN_SECONDS = 330  # the longest run (300 s) plus slack
MIN_TOKEN_SECONDS = 60


def load_or_create_key(path: Path) -> Ed25519PrivateKey:
    """The platform's token-signing key; created once, private to its owner."""
    if path.exists():
        key = serialization.load_pem_private_key(path.read_bytes(), None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError(f"{path} must hold an Ed25519 private key")
        return key
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    return key


def load_keys(path: Path) -> tuple[Ed25519PrivateKey, Ed25519PrivateKey | None]:
    """The signing key, and the previous one if a rotation is in progress.

    Rotate by renaming ``data-token.pem`` to ``data-token.previous.pem`` and restarting: new tokens
    use a fresh key, both public keys are published, and once the longest token (5 minutes) has
    expired the previous file can be deleted.
    """
    current = load_or_create_key(path)
    previous_path = path.with_name(path.stem + ".previous" + path.suffix)
    if not previous_path.exists():
        return current, None
    previous = serialization.load_pem_private_key(previous_path.read_bytes(), None)
    if not isinstance(previous, Ed25519PrivateKey):
        raise ValueError(f"{previous_path} must hold an Ed25519 private key")
    return current, previous


def _kid(key: Ed25519PrivateKey) -> str:
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return hashlib.sha256(public).hexdigest()[:16]


class DataTokenMinter:
    def __init__(
        self,
        key: Ed25519PrivateKey,
        *,
        issuer: str,
        audience: str,
        previous: Ed25519PrivateKey | None = None,
    ):
        self.key, self.issuer, self.audience = key, issuer, audience
        self.previous = previous
        self.kid = _kid(key)

    def jwks(self) -> dict[str, object]:
        """Public keys only: the current one and, while rotating, the previous one."""
        keys = []
        for key in (self.key, self.previous):
            if key is not None:
                jwk = jwt.algorithms.OKPAlgorithm.to_jwk(key.public_key(), as_dict=True)
                keys.append({**jwk, "kid": _kid(key), "use": "sig", "alg": "EdDSA"})
        return {"keys": keys}

    def mint(self, scope: RunScope, ttl_seconds: int) -> str | None:
        """A read-only token for this run's user, or None when the run has no user or tenant."""
        if not scope.user_id or not scope.tenant_id:
            return None
        now = int(time.time())
        agent = scope.agent_id or "primary"
        claims = {
            "iss": self.issuer,
            "aud": self.audience,
            "sub": scope.user_id,
            "iat": now,
            "nbf": now,
            "exp": now + max(MIN_TOKEN_SECONDS, min(ttl_seconds, MAX_TOKEN_SECONDS)),
            # A unique id the data service records with every statement it runs for this token.
            "jti": uuid.uuid4().hex,
            # Tenant-wide grants in the data service are made to this group.
            "groups": [f"tenant-{scope.tenant_id}"],
            "scope": "data:read",
            "act": {"sub": f"agent-substrate:{agent}"},
            "tenant_id": scope.tenant_id,
            "thread_id": scope.thread_id,
            "agent_id": agent,
        }
        return jwt.encode(
            claims, self.key, algorithm="EdDSA", headers={"kid": self.kid}
        )


# What a sandbox may ask of the data service: browse what it may read, run SELECTs, cancel its own.
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
MAX_REQUEST_BYTES = 1 << 20  # a SQL statement, not a file


def problem(status: int, kind: str, detail: str) -> tuple[int, bytes]:
    """An RFC 9457 problem document, the same shape the data service itself returns."""
    body = {
        "type": f"https://substrate.dev/problems/{kind}",
        "title": kind.replace("-", " "),
        "status": status,
        "detail": detail,
    }
    return status, json.dumps(body).encode()


class DataGateway:
    """The sandbox's one door: an allow-listed, TLS-verified HTTP forwarder on a Unix socket."""

    def __init__(
        self,
        directory: Path,
        upstream_url: str,
        *,
        ca_file: str | None = None,
        client_cert_file: str | None = None,
        client_key_file: str | None = None,
        max_connections: int = 64,
        connect_seconds: float = 10,
        read_seconds: float = 600,
        client: httpx.AsyncClient | None = None,
    ):
        parts = urlsplit(upstream_url)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username
            or parts.query
            or parts.fragment
        ):
            raise ValueError("The data service URL must be http(s)://host[:port]")
        if parts.scheme == "http" and parts.hostname not in LOOPBACK:
            raise ValueError(
                "The data service is on another machine: its URL must be https:// "
                "(plain http is only allowed to localhost)."
            )
        if bool(client_cert_file) != bool(client_key_file):
            raise ValueError(
                "DATA_CLIENT_CERT_FILE and DATA_CLIENT_KEY_FILE go together"
            )
        self.upstream = upstream_url.rstrip("/")
        self.directory, self.socket_path = directory, directory / "api.sock"
        if len(str(self.socket_path)) > UNIX_PATH_LIMIT:
            raise ValueError(
                f"Gateway path is too long for a Unix socket: {self.socket_path}"
            )
        context: ssl.SSLContext | bool = False
        if parts.scheme == "https":
            context = ssl.create_default_context(cafile=ca_file or None)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            if client_cert_file and client_key_file:
                context.load_cert_chain(client_cert_file, client_key_file)
        self.client = (
            client
            or httpx.AsyncClient(
                verify=context if parts.scheme == "https" else True,
                timeout=httpx.Timeout(
                    connect=connect_seconds, read=read_seconds, write=30, pool=5
                ),
                limits=httpx.Limits(
                    max_connections=max_connections,
                    max_keepalive_connections=16,
                    keepalive_expiry=30,
                ),
                follow_redirects=False,
                trust_env=False,  # the data service address is configuration, not an ambient proxy
            )
        )
        # One client serves every run: it must never keep state (cookies) from one run's response for the next.
        self.client.cookies.jar.set_policy(DefaultCookiePolicy(allowed_domains=[]))
        self.slots = asyncio.Semaphore(max_connections)
        self.server: uvicorn.Server | None = None
        self.task: asyncio.Task[None] | None = None
        self.start_lock = asyncio.Lock()

    # ── lifecycle ────────────────────────────────────────────────────────────
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
                await self.forward(
                    scope, receive, send
                )  # a plain function: uvicorn's ASGI3 test

            config = uvicorn.Config(
                app,
                lifespan="off",
                log_level="warning",
                access_log=False,
                server_header=False,
                timeout_keep_alive=60,
                timeout_graceful_shutdown=2,
            )
            self.server = _EmbeddedServer(config)
            self.task = asyncio.create_task(self.server.serve(sockets=[listener]))
            while not self.server.started and not self.task.done():
                await asyncio.sleep(0.01)
            logger.info("Data gateway on %s -> %s", self.socket_path, self.upstream)
        await self.probe()

    async def probe(self) -> None:
        """Say so at startup if the data service is unreachable or its certificate is rejected."""
        try:
            response = await self.client.get(self.upstream + "/livez", timeout=10)
            response.raise_for_status()
        except (httpx.HTTPError, ssl.SSLError) as exc:
            logger.warning(
                "Data service %s is not reachable from here: %s. Check DATA_SERVICE_URL, "
                "DATA_SERVICE_CA_FILE and the client certificate.",
                self.upstream,
                exc,
            )

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

    # ── the forwarder (ASGI) ─────────────────────────────────────────────────
    async def forward(self, scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        if scope["type"] != "http":
            return
        method, path = scope["method"], scope["path"]
        dotted = {".", ".."} & set(path.split("/"))
        if dotted or not any(m == method and rx.match(path) for m, rx in ALLOWED):
            await self.reply(
                send,
                *problem(
                    403,
                    "not-allowed-from-sandbox",
                    "This request is not available from a sandbox",
                ),
            )
            return
        query = scope.get("query_string", b"")
        if len(query) > 2048:
            await self.reply(
                send, *problem(414, "uri-too-long", "Query string too long")
            )
            return
        body = b""
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return
            body += message.get("body", b"")
            if len(body) > MAX_REQUEST_BYTES:
                await self.reply(
                    send, *problem(413, "request-too-large", "Request body too large")
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
        if self.slots.locked():
            await self.reply(
                send,
                *problem(503, "gateway-busy", "Too many concurrent data requests"),
                {"retry-after": "1"},
            )
            return
        async with self.slots:
            url = (
                self.upstream + path + ("?" + query.decode("latin-1") if query else "")
            )
            try:
                request = self.client.build_request(
                    method, url, headers=headers, content=body or None
                )
                response = await self.client.send(request, stream=True)
            except httpx.TimeoutException:
                await self.reply(
                    send,
                    *problem(
                        504,
                        "upstream-timeout",
                        "The data service did not answer in time",
                    ),
                )
                return
            except (httpx.HTTPError, ssl.SSLError) as exc:
                logger.warning("Data service request failed: %s", type(exc).__name__)
                await self.reply(
                    send,
                    *problem(
                        502,
                        "upstream-unavailable",
                        "The data service could not be reached",
                    ),
                )
                return
            try:
                await send(
                    {
                        "type": "http.response.start",
                        "status": response.status_code,
                        "headers": [
                            (k.encode(), v.encode())
                            for k, v in response.headers.items()
                            if k.lower() in FORWARDED_RESPONSE_HEADERS
                        ],
                    }
                )
                async for chunk in response.aiter_raw():
                    await send(
                        {"type": "http.response.body", "body": chunk, "more_body": True}
                    )
                await send(
                    {"type": "http.response.body", "body": b"", "more_body": False}
                )
            except (httpx.HTTPError, OSError):
                return  # the sandbox went away or the stream broke; the connection just closes
            finally:
                await response.aclose()

    @staticmethod
    async def reply(
        send, status: int, body: bytes, extra: dict[str, str] | None = None
    ) -> None:  # type: ignore[no-untyped-def]
        headers = [(b"content-type", b"application/problem+json")]
        headers += [(k.encode(), v.encode()) for k, v in (extra or {}).items()]
        await send(
            {"type": "http.response.start", "status": status, "headers": headers}
        )
        await send({"type": "http.response.body", "body": body})


class _EmbeddedServer(uvicorn.Server):
    """Runs inside another program's event loop without touching that program's signal handlers."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


class PlatformDataAccess:
    """The library's ``DataAccess`` for this platform."""

    def __init__(
        self,
        *,
        service_url: str,
        relay_dir: Path,
        minter: DataTokenMinter,
        gateway: DataGateway | None = None,
    ):
        self.service_url = service_url.rstrip("/")
        self.minter = minter
        self.gateway = gateway or DataGateway(relay_dir, service_url)

    async def grant(self, scope: RunScope, *, timeout_s: int) -> DataMount | None:
        token = self.minter.mint(scope, timeout_s + 30)
        if token is None:
            return None
        await self.gateway.ensure_started()
        return DataMount(socket_dir=str(self.gateway.directory), token=token)

    async def token_for(self, scope: RunScope, ttl_seconds: int = 120) -> str | None:
        """A read-only token for server-side callers (the catalog tool), under the same rules."""
        return self.minter.mint(scope, ttl_seconds)

    async def stop(self) -> None:
        await self.gateway.stop()


def sandbox_has_client(python_bin: str) -> bool:
    """Whether the interpreter that runs agent code can import the data client."""
    try:
        probe = subprocess.run(  # noqa: S603 - fixed argv; python_bin is deployment config
            [python_bin, "-c", "import substrate_data_client"],
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


def build_data_access(
    *,
    service_url: str,
    data_dir: str,
    key_file: str,
    relay_dir: str,
    issuer: str,
    audience: str,
    python_bin: str,
    ca_file: str = "",
    client_cert_file: str = "",
    client_key_file: str = "",
) -> PlatformDataAccess | None:
    """Data access for the sandbox, or None (with the reason logged) when it cannot work."""
    if not service_url:
        return None
    if not sandbox_has_client(python_bin):
        logger.warning(
            "DATA_SERVICE_URL is set but %s cannot import substrate_data_client: install the client "
            "package there (substrate-data/client). Sandbox data access is disabled.",
            python_bin,
        )
        return None
    key_path = Path(key_file or f"{data_dir.rstrip('/')}/keys/data-token.pem").resolve()
    current, previous = load_keys(key_path)
    minter = DataTokenMinter(
        current, issuer=issuer, audience=audience, previous=previous
    )
    directory = Path(relay_dir or f"/var/tmp/substrate-data-relay-{os.getuid()}")
    gateway = DataGateway(
        directory,
        service_url,
        ca_file=ca_file or None,
        client_cert_file=client_cert_file or None,
        client_key_file=client_key_file or None,
    )
    logger.info(
        "Sandbox data access on. Tell the data service to trust this platform: "
        'SUBSTRATE_DATA_TRUSTED_ISSUERS=[{"issuer": "%s", "audience": "%s", '
        '"jwks_uri": "https://<this backend\'s public address>%s"}]',
        issuer,
        audience,
        JWKS_PATH,
    )
    return PlatformDataAccess(
        service_url=service_url, relay_dir=directory, minter=minter, gateway=gateway
    )
