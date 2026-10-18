"""Governed warehouse data for the code interpreter, when data and agents run on different machines.

The data service is a remote HTTPS service. Nothing here assumes it shares a host, a disk or a
network with this process. Four small parts, composed by ``factory.py``:

* :class:`DataTokenMinter` signs a short-lived token for one run: the user, their tenant (the Keycloak organization id)
  and the acting agent, with an Ed25519 key only this platform holds. It signs only for the principal the run recorded
  (``RunScope``), only after confirming the user is still a member of that organization (:class:`KeycloakMembership`,
  cached for 60 s), asserts no groups (the data service derives the tenant itself) and never grants a scope outside
  its ``allowed_scopes`` (the same names ``substrate_data.auth.scopes`` enforces per route). Its public keys (current and,
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
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, urlsplit

import httpx
import jwt
import uvicorn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from substrate.integrations.data import DataFilesConnection
from substrate_data_client import AsyncDataClient, ClientCredentials
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


# The scopes a data token may carry; the data service enforces them per route (substrate_data.auth.scopes.SCOPES).
DATA_SCOPES = frozenset(
    {
        "data:read",
        "files:read",
        "files:write",
        "documents:read",
        "documents:write",
        "memory:read",
        "memory:write",
    }
)
MEMBERSHIP_TTL_SECONDS = 60


class MembershipChecker(Protocol):
    async def is_member(self, tenant_id: str, user_id: str) -> bool: ...


class KeycloakMembership:
    """Whether a user is (still) a member of an organization, asked of Keycloak's admin API and cached for 60 s.

    Uses a service-account client of the realm (client credentials; it needs ``view-users``). Fails closed: any
    answer other than a clear yes or no is "not a member", and is not cached.
    """

    def __init__(
        self,
        issuer: str,
        client_id: str,
        client_secret: str,
        *,
        ca_file: str | None = None,
        ttl: float = MEMBERSHIP_TTL_SECONDS,
        client: httpx.AsyncClient | None = None,
        clock=time.monotonic,
    ):
        base, _, realm = issuer.rstrip("/").rpartition("/realms/")
        if not base or not realm:
            raise ValueError(
                f"{issuer!r} is not a Keycloak realm URL (…/realms/<realm>)"
            )
        self.admin = f"{base}/admin/realms/{realm}"
        self.token_url = f"{issuer.rstrip('/')}/protocol/openid-connect/token"
        self.client_id, self.client_secret = client_id, client_secret
        self.ttl, self.clock = ttl, clock
        self.http = client or httpx.AsyncClient(
            verify=ssl.create_default_context(cafile=ca_file) if ca_file else True,
            timeout=httpx.Timeout(5),
            follow_redirects=False,
        )
        self._cache: dict[tuple[str, str], tuple[bool, float]] = {}
        self._token: tuple[str, float] | None = None

    async def _bearer(self) -> str:
        now = self.clock()
        if self._token and self._token[1] > now:
            return self._token[0]
        response = await self.http.post(
            self.token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
        )
        response.raise_for_status()
        body = response.json()
        self._token = (
            body["access_token"],
            now + max(5.0, float(body.get("expires_in", 60)) - 10),
        )
        return self._token[0]

    async def is_member(self, tenant_id: str, user_id: str) -> bool:
        key = (tenant_id, user_id)
        now = self.clock()
        hit = self._cache.get(key)
        if hit is not None and hit[1] > now:
            return hit[0]
        try:
            response = await self.http.get(
                f"{self.admin}/organizations/{quote(tenant_id, safe='')}/members/{quote(user_id, safe='')}",
                headers={"Authorization": f"Bearer {await self._bearer()}"},
            )
        except (httpx.HTTPError, KeyError, ValueError):
            logger.warning(
                "Organization membership could not be checked; refusing the data token"
            )
            return False
        if response.status_code == 200:
            member = True
        elif response.status_code == 404:
            member = False
        else:
            logger.warning(
                "Organization membership check answered %s; refusing",
                response.status_code,
            )
            return False
        self._cache[key] = (member, now + self.ttl)
        if len(self._cache) > 10_000:
            self._cache = {k: v for k, v in self._cache.items() if v[1] > now}
        return member

    async def aclose(self) -> None:
        await self.http.aclose()


class DataTokenMinter:
    def __init__(
        self,
        key: Ed25519PrivateKey,
        *,
        issuer: str,
        audience: str,
        membership: MembershipChecker,
        previous: Ed25519PrivateKey | None = None,
        allowed_scopes: frozenset[str] = DATA_SCOPES,
        files_issuer: str = "",
    ):
        unknown = set(allowed_scopes) - DATA_SCOPES
        if unknown:
            raise ValueError(f"unknown data scopes: {sorted(unknown)}")
        self.key, self.issuer, self.audience = key, issuer, audience
        # The same key under a second issuer name: the data service trusts that name to change files, and `issuer` only to read.
        self.files_issuer = files_issuer or issuer
        self.previous = previous
        self.membership = membership
        self.allowed_scopes = frozenset(allowed_scopes)
        self.kid = _kid(key)

    def jwks(self) -> dict[str, object]:
        """Public keys only: the current one and, while rotating, the previous one."""
        keys = []
        for key in (self.key, self.previous):
            if key is not None:
                jwk = jwt.algorithms.OKPAlgorithm.to_jwk(key.public_key(), as_dict=True)
                keys.append({**jwk, "kid": _kid(key), "use": "sig", "alg": "EdDSA"})
        return {"keys": keys}

    async def mint(
        self,
        scope: RunScope,
        ttl_seconds: int,
        *,
        scopes: Iterable[str] = ("data:read",),
        issuer: str | None = None,
    ) -> str | None:
        """A token for the run's user, or None when the run has no user or tenant or the user is not a member.

        ``scope`` is the run's recorded principal (set by the platform when the run started, never by the model). The
        token asserts no groups: the data service derives the tenant from ``tenant_id``.
        """
        if not scope.user_id or not scope.tenant_id:
            return None
        asked = frozenset(scopes)
        if not asked or not asked <= self.allowed_scopes:
            raise ValueError(
                f"scopes {sorted(asked - self.allowed_scopes) or ['(none)']} are outside this issuer's allowed scopes"
            )
        if not await self.membership.is_member(scope.tenant_id, scope.user_id):
            logger.warning(
                "Refusing a data token: the run's user is not a member of its organization"
            )
            return None
        now = int(time.time())
        agent = scope.agent_id or "primary"
        claims = {
            "iss": issuer or self.issuer,
            "aud": self.audience,
            "sub": scope.user_id,
            "iat": now,
            "nbf": now,
            "exp": now + max(MIN_TOKEN_SECONDS, min(ttl_seconds, MAX_TOKEN_SECONDS)),
            # A unique id the data service records with every statement it runs for this token.
            "jti": uuid.uuid4().hex,
            "scope": " ".join(sorted(asked)),
            "act": {"sub": f"agent-substrate:{agent}"},
            "tenant_id": scope.tenant_id,
            "thread_id": scope.thread_id,
            "agent_id": agent,
        }
        return jwt.encode(
            claims, self.key, algorithm="EdDSA", headers={"kid": self.kid}
        )

    async def mint_files(self, scope: RunScope, ttl_seconds: int = 300) -> str:
        """A delegated token that may read and write the user's own files (``files:write``): what ``DataFileStore`` sends.

        Signed under ``files_issuer``, the name the data service trusts to change files. Raises ``ValueError`` without a user
        and a tenant (files belong to someone) or when the user is not a member of the organization."""
        token = await self.mint(
            scope, ttl_seconds, scopes=("files:write",), issuer=self.files_issuer
        )
        if token is None:
            raise ValueError("a files token needs a user and a tenant")
        return token


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
        token = await self.minter.mint(scope, timeout_s + 30)
        if token is None:
            return None
        await self.gateway.ensure_started()
        return DataMount(socket_dir=str(self.gateway.directory), token=token)

    async def token_for(self, scope: RunScope, ttl_seconds: int = 120) -> str | None:
        """A read-only token for server-side callers (the catalog tool), under the same rules."""
        return await self.minter.mint(scope, ttl_seconds)

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
    membership: MembershipChecker | None = None,
    allowed_scopes: frozenset[str] = DATA_SCOPES,
) -> PlatformDataAccess | None:
    """Data access for the sandbox, or None (with the reason logged) when it cannot work.

    Without a ``membership`` checker (no OIDC admin client configured) no token could be signed safely, so it is off.
    """
    if not service_url:
        return None
    if membership is None:
        logger.warning(
            "DATA_SERVICE_URL is set but no organization-membership check is configured "
            "(OIDC_ISSUER, OIDC_ADMIN_CLIENT_ID, OIDC_ADMIN_CLIENT_SECRET). Sandbox data access is disabled."
        )
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
        current,
        issuer=issuer,
        audience=audience,
        previous=previous,
        membership=membership,
        allowed_scopes=allowed_scopes,
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
        '"jwks_uri": "https://<this backend\'s public address>%s", "allowed_scopes": %s}]',
        issuer,
        audience,
        JWKS_PATH,
        json.dumps(sorted(allowed_scopes)),
    )
    return PlatformDataAccess(
        service_url=service_url, relay_dir=directory, minter=minter, gateway=gateway
    )


def build_file_connection(
    *,
    service_url: str,
    data_dir: str,
    key_file: str,
    issuer: str,
    files_issuer: str,
    audience: str,
    ca_file: str = "",
    client_cert_file: str = "",
    client_key_file: str = "",
    admin_issuer: str = "",
    admin_client_id: str = "",
    admin_client_secret: str = "",
) -> tuple[DataFilesConnection, DataTokenMinter, AsyncDataClient | None]:
    """The pooled connection every file operation goes through, and the minter whose public keys the data service must fetch.

    Same address rules and TLS as the sandbox gateway: https:// unless the service is on this machine, a private CA and a client
    certificate when configured, no ambient proxy, no redirects.
    """
    parts = urlsplit(service_url)
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
        raise ValueError("DATA_CLIENT_CERT_FILE and DATA_CLIENT_KEY_FILE go together")
    context: ssl.SSLContext | bool = True
    if parts.scheme == "https":
        context = ssl.create_default_context(cafile=ca_file or None)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        if client_cert_file and client_key_file:
            context.load_cert_chain(client_cert_file, client_key_file)
    key_path = Path(key_file or f"{data_dir.rstrip('/')}/keys/data-token.pem").resolve()
    current, previous = load_keys(key_path)
    minter = DataTokenMinter(
        current,
        issuer=issuer,
        audience=audience,
        previous=previous,
        files_issuer=files_issuer,
    )

    def transport() -> httpx.AsyncHTTPTransport:
        return httpx.AsyncHTTPTransport(
            verify=context,
            limits=httpx.Limits(
                max_connections=64, max_keepalive_connections=16, keepalive_expiry=30
            ),
            trust_env=False,
        )

    connection = DataFilesConnection(
        service_url, tokens=minter.mint_files, transport=transport()
    )
    admin: AsyncDataClient | None = None
    if admin_client_id and admin_client_secret and admin_issuer:
        # The administrator is an OIDC client of its own (data-admins group): its token is never a user's, and never delegated.
        admin = AsyncDataClient(
            service_url,
            token=ClientCredentials(admin_issuer, admin_client_id, admin_client_secret),
            transport=transport(),
        )
    logger.info(
        "Files go through the data service at %s. Tell it to trust this platform to change files: "
        'SUBSTRATE_DATA_TRUSTED_ISSUERS=[{"issuer": "%s", "audience": "%s", "read_only": false, '
        '"jwks_uri": "https://<this backend\'s public address>%s"}]',
        service_url,
        files_issuer,
        audience,
        JWKS_PATH,
    )
    return connection, minter, admin
