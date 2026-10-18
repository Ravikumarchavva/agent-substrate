"""Test infrastructure.

Unit tests need nothing. Integration tests start a throwaway SeaweedFS 4.48 (`weed mini`, S3 +
Iceberg REST catalog) in Docker, and run the controller, workers and API as real HTTP services
in threads. Workers are operated by the *test harness*, standing in for compute an operator runs
elsewhere: the application never starts them. Identity comes from a test RSA key whose public
half is served as the JWKS, exactly as an IdP would.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import jwt
import pytest
import uvicorn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import SecretStr

from loamz.data.server.kernel.auth import TokenVerifier
from loamz.data.server.kernel.settings import Settings, TrustedIssuer

ISSUER = "http://127.0.0.1:1/realms/test"
SEAWEED_IMAGE = "chrislusf/seaweedfs:4.48@sha256:4e61d15fd35994cb1e43e1e553dff106794841fd9a99ade2fc8c8bfce4d7872d"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# ------------------------------------------------------------------ identity
class StaticJWKS:
    """Stands in for the IdP's JWKS endpoint."""

    def __init__(self, public_key: Any):
        self.key = public_key

    def get_signing_key_from_jwt(self, token: str) -> Any:
        return type("Key", (), {"key": self.key})()


AGENT_ISSUER = "loamz-agents-test"
# The same loamz-agents keys, trusted for tokens that may change data (files:write); AGENT_ISSUER is read-only.
AGENT_ISSUER_WRITABLE = "loamz-agents-test-writable"


class IssuerKeys:
    """loamz-agents's public key endpoint, on its own port: what the data service fetches."""

    def __init__(self, keys: dict[str, Any] | None = None):
        self.keys: dict[str, Any] = keys or {}
        self.hits = 0
        self.failing = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                owner.hits += 1
                if owner.failing:
                    self.send_response(503)
                    self.end_headers()
                    return
                body = json.dumps({"keys": list(owner.keys.values())}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def uri(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/.well-known/jwks.json"

    def publish(self, kid: str, private_key: Any) -> None:
        jwk = jwt.algorithms.OKPAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
        self.keys[kid] = {**jwk, "kid": kid, "use": "sig", "alg": "EdDSA"}


@dataclass
class Identity:
    private_key: Any
    jwks: StaticJWKS
    agent_key: Any = None
    agent_keys: IssuerKeys | None = None

    def agent_token(
        self,
        user: str,
        tenant: str = "acme",
        *,
        key: Any = None,
        issuer: str | None = None,
        audience: str = "loamz-data",
        expires_in: int = 120,
        scopes: tuple[str, ...] | None = None,
        **extra: Any,
    ) -> str:
        """A token as loamz-agents mints it for one run: signed by its own key.

        `scopes` defaults to `data:read`. A token holding a files:* scope is minted by the writable issuer
        (the read-only issuer's tokens can never change anything, whatever they claim)."""
        if issuer is None:
            writable = scopes and any(
                s.startswith(("files:", "documents:", "memory:")) for s in scopes
            )
            issuer = AGENT_ISSUER_WRITABLE if writable else AGENT_ISSUER
        if scopes is not None:
            extra.setdefault("scope", " ".join(scopes))
        now = int(time.time())
        claims = {
            "iss": issuer,
            "aud": audience,
            "sub": user,
            "groups": [f"tenant-{tenant}"],
            "scope": "data:read",
            "act": {"sub": "loamz-agents:primary"},
            "tenant_id": tenant,
            "thread_id": "thread-1",
            "run_id": "run-1",
            "iat": now,
            "exp": now + expires_in,
            **extra,
        }
        return jwt.encode(
            claims, key or self.agent_key, algorithm="EdDSA", headers={"kid": "agent-1"}
        )

    def token(
        self,
        name: str,
        groups: tuple[str, ...] = (),
        *,
        audience: str = "loamz-data",
        issuer: str = ISSUER,
        expires_in: int = 300,
        **extra: Any,
    ) -> str:
        now = int(time.time())
        claims = {
            "iss": issuer,
            "aud": audience,
            "sub": f"sub-{name}",
            "preferred_username": name,
            "groups": list(groups),
            "typ": "Bearer",
            "iat": now,
            "exp": now + expires_in,
            **extra,
        }
        return jwt.encode(claims, self.private_key, algorithm="RS256", headers={"kid": "test"})


@pytest.fixture(scope="session")
def identity(tmp_path_factory) -> Identity:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    agent = Ed25519PrivateKey.generate()
    issuer_keys = IssuerKeys()
    issuer_keys.publish("agent-1", agent)
    return Identity(key, StaticJWKS(key.public_key()), agent, issuer_keys)


@pytest.fixture(scope="session")
def ticket_keys(tmp_path_factory) -> tuple[Path, Path]:
    folder = tmp_path_factory.mktemp("keys")
    key = Ed25519PrivateKey.generate()
    private, public = folder / "ticket.pem", folder / "ticket.pub.pem"
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    return private, public


# ------------------------------------------------------------------ storage
STS_ROLE = "arn:aws:iam::role/DataReader"


@dataclass
class Seaweed:
    s3: str
    iceberg: str
    access_key: str
    secret_key: str
    sts_role: str = STS_ROLE
    # What attached compute holds: reads/writes the warehouse, reads staging, nothing on the volumes bucket.
    worker_access_key: str = ""
    worker_secret_key: str = ""


VOLUMES_BUCKET = "loamz-volumes"


def write_storage_config(
    directory: Path, access: str, secret: str, worker: tuple[str, str] = ("", "")
) -> None:
    """S3 identities plus an STS role that can read anything; session policies narrow it per call."""
    identities = [
        {
            "name": "admin",
            "credentials": [{"accessKey": access, "secretKey": secret}],
            "actions": ["Admin", "Read", "Write", "List", "Tagging"],
        }
    ]
    if worker[0]:
        # Like scripts/bootstrap.py: compute works on the warehouse and staging, never on user files.
        identities.append(
            {
                "name": "loamz-worker",
                "credentials": [{"accessKey": worker[0], "secretKey": worker[1]}],
                "actions": [
                    *(f"{a}:warehouse" for a in ("Read", "Write", "List")),
                    *(f"{a}:loamz-staging" for a in ("Read", "List")),
                ],
            }
        )
    (directory / "s3.json").write_text(json.dumps({"identities": identities}))
    (directory / "iam.json").write_text(
        json.dumps(
            {
                "sts": {
                    "tokenDuration": "1h",
                    "maxSessionLength": "12h",
                    "issuer": "seaweedfs-sts",
                    "signingKey": __import__("base64").b64encode(os.urandom(32)).decode(),
                },
                "policy": {"defaultEffect": "Deny", "storeType": "memory"},
                "roles": [
                    {
                        "roleName": "DataReader",
                        "roleArn": STS_ROLE,
                        "trustPolicy": {
                            "Version": "2012-10-17",
                            "Statement": [
                                {
                                    "Effect": "Allow",
                                    "Principal": {"AWS": "*"},
                                    "Action": "sts:AssumeRole",
                                }
                            ],
                        },
                        "attachedPolicies": ["ReadAll"],
                    }
                ],
                "policies": [
                    {
                        "name": "ReadAll",
                        "document": {
                            "Version": "2012-10-17",
                            "Statement": [
                                {
                                    "Effect": "Allow",
                                    "Action": ["s3:GetObject", "s3:ListBucket"],
                                    "Resource": ["arn:aws:s3:::*"],
                                }
                            ],
                        },
                    }
                ],
            }
        )
    )
    directory.chmod(0o755)
    for name in ("s3.json", "iam.json"):
        (directory / name).chmod(0o644)


@pytest.fixture(scope="session")
def seaweed(tmp_path_factory) -> Seaweed:
    if os.getenv("LOAMZ_DATA_SKIP_INTEGRATION") or shutil.which("docker") is None:
        pytest.skip("Integration tests need Docker (or unset LOAMZ_DATA_SKIP_INTEGRATION)")
    s3_port, iceberg_port = free_port(), free_port()
    access, secret = "TEST" + secrets.token_hex(8).upper(), secrets.token_urlsafe(24)
    name = f"loamz-test-{uuid4().hex[:8]}"
    config = tmp_path_factory.mktemp("storage-config")
    worker = ("TESTW" + secrets.token_hex(8).upper(), secrets.token_urlsafe(24))
    write_storage_config(config, access, secret, worker)
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "-v",
            f"{config}:/cfg:ro",
            "-p",
            f"127.0.0.1:{s3_port}:8333",
            "-p",
            f"127.0.0.1:{iceberg_port}:8181",
            SEAWEED_IMAGE,
            "mini",
            "-dir=/data",
            "-s3.config=/cfg/s3.json",
            "-s3.iam.config=/cfg/iam.json",
            "-bucket=loamz-staging",
            "-tableBucket=warehouse",
            "-master.telemetry=false",
        ],
        check=True,
        capture_output=True,
    )
    deadline = time.monotonic() + 90
    url = f"http://127.0.0.1:{iceberg_port}/v1/config"
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    else:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        pytest.fail("SeaweedFS did not start")
    import boto3
    from botocore.client import Config

    boto3.client(
        "s3",
        endpoint_url=f"http://127.0.0.1:{s3_port}",
        aws_access_key_id=access,
        aws_secret_access_key=secret,
        region_name="us-east-1",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    ).create_bucket(Bucket=VOLUMES_BUCKET)
    yield Seaweed(
        f"http://127.0.0.1:{s3_port}",
        f"http://127.0.0.1:{iceberg_port}",
        access,
        secret,
        worker_access_key=worker[0],
        worker_secret_key=worker[1],
    )
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)


# ------------------------------------------------------------------ services
class Served:
    def __init__(self, app: Any, port: int | None = None):
        self.port = port or free_port()
        self.server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning")
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 30
        while not self.server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert self.server.started, "service did not start"

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=15)


@dataclass
class Stack:
    api: str
    controller: str
    settings: Settings
    worker_settings: Settings
    identity: Identity
    workers: dict[str, Served]
    resources: dict[str, str]
    app: Any = None

    def token(self, name: str, *groups: str) -> str:
        return self.identity.token(name, groups)

    def client(self, name: str, *groups: str):
        from loamz.data.client import DataClient

        return DataClient(self.api, token=lambda: self.token(name, *groups))

    def member(self, name: str, tenant: str, *groups: str, **claims: Any):
        """A client signed in to the organization `tenant` (its Keycloak `organization` claim)."""
        from loamz.data.client import DataClient

        return DataClient(
            self.api,
            token=lambda: self.identity.token(name, groups, organization=[tenant], **claims),
        )

    def agent_client(self, user: str, tenant: str = "acme", **claims: Any):
        """A client holding an agent's delegated token; `scopes=("files:write", …)` sets its scopes."""
        from loamz.data.client import DataClient

        return DataClient(self.api, token=lambda: self.identity.agent_token(user, tenant, **claims))

    def http(self, name: str | None, *groups: str) -> httpx.Client:
        headers = {"Authorization": "Bearer " + self.token(name, *groups)} if name else {}
        return httpx.Client(base_url=self.api + "/api/v1", headers=headers, timeout=120)


def wait_ready(controller: httpx.Client, resource_id: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resources = controller.get("/resources").json()["resources"]
        if next(r for r in resources if r["id"] == resource_id)["state"] == "ready":
            return
        time.sleep(0.1)
    raise AssertionError(f"resource {resource_id} did not become ready")


# ------------------------------------------------------------------ PostgreSQL catalog
# `LOAMZ_DATA_TEST_CATALOG=postgres` runs the whole suite on a throwaway PostgreSQL instead of SQLite.
USE_POSTGRES = os.getenv("LOAMZ_DATA_TEST_CATALOG") == "postgres"
# `LOAMZ_DATA_TEST_VENDING=1` runs the suite with credential vending: the SQL worker holds no storage key.
USE_VENDING = os.getenv("LOAMZ_DATA_TEST_VENDING") == "1"


class Postgres:
    def __init__(self, port: int, name: str):
        self.port, self.name = port, name
        self.admin = f"postgresql://postgres:test@127.0.0.1:{port}/postgres"

    def database(self) -> str:
        """A new empty database; returns its connection URL."""
        import psycopg

        database = "t" + uuid4().hex[:12]
        with psycopg.connect(self.admin, autocommit=True) as conn:
            conn.execute(f"CREATE DATABASE {database}")
        return self.admin.rsplit("/", 1)[0] + "/" + database


def start_postgres(image: str = "postgres:16-alpine") -> Postgres:
    import psycopg

    if not shutil.which("docker"):
        pytest.skip("Docker is required for the PostgreSQL tests")
    port, name = free_port(), "loamz-data-pg-" + uuid4().hex[:8]
    subprocess.run(
        [
            "docker", "run", "-d", "--rm", "--name", name, "-e", "POSTGRES_PASSWORD=test",
            "-p", f"127.0.0.1:{port}:5432", image,
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip
    server = Postgres(port, name)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            psycopg.connect(server.admin, connect_timeout=2).close()
            return server
        except psycopg.Error:
            time.sleep(0.5)
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    pytest.fail("PostgreSQL did not start")


@pytest.fixture(scope="session")
def postgres():
    """A throwaway PostgreSQL when the suite is run against it (None for SQLite)."""
    if not USE_POSTGRES:
        yield None
        return
    server = start_postgres()
    yield server
    subprocess.run(["docker", "rm", "-f", server.name], capture_output=True)


@pytest.fixture
def make_store(postgres, tmp_path):
    """Builds a CatalogStore on whichever backend this run is testing."""
    from loamz.data.server.kernel.metadata import CatalogStore

    created = []

    def build():
        store = (
            CatalogStore(url=postgres.database())
            if postgres
            else CatalogStore(tmp_path / f"catalog-{len(created)}.sqlite")
        )
        created.append(store)
        return store

    yield build
    for store in created:
        store.close()


@pytest.fixture(scope="module")
def stack(seaweed, identity, ticket_keys, tmp_path_factory, postgres) -> Stack:
    from loamz.data.server.app import create_app
    from loamz.data.server.features.compute.control_api import create_control_app
    from loamz.data.server.features.compute.worker import create_compute_app
    from loamz.data.server.kernel.container import Container

    root = tmp_path_factory.mktemp("stack")
    private, public = ticket_keys
    controller_token, worker_token = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
    base = dict(
        _env_file=None,
        s3_endpoint_url=seaweed.s3,
        iceberg_catalog_url=seaweed.iceberg,
        s3_access_key_id=SecretStr(seaweed.access_key),
        s3_secret_access_key=SecretStr(seaweed.secret_key),
        oidc_issuer=ISSUER,
        metadata=root / "catalog.sqlite",
        **({"metadata_url": SecretStr(postgres.database())} if postgres else {}),
        scratch=root / "tmp",
        controller_state=root / "compute",
        worker_threads=2,
        worker_memory_limit="1GB",
        mask_salt=SecretStr("test-salt-9f3a"),
    )
    controller_settings = Settings(**base, controller_token=SecretStr(controller_token))
    controller = Served(create_control_app(controller_settings))
    worker_settings = Settings(
        **{
            **base,
            "s3_access_key_id": SecretStr(seaweed.worker_access_key),
            "s3_secret_access_key": SecretStr(seaweed.worker_secret_key),
        },
        worker_token=SecretStr(worker_token),
        ticket_public_key_file=public,
    )
    keyless = worker_settings.model_copy(
        update={"s3_access_key_id": SecretStr(""), "s3_secret_access_key": SecretStr("")}
    )
    workers = {
        role: Served(
            create_compute_app(keyless if USE_VENDING and role == "sql" else worker_settings, role)
        )
        for role in ("sql", "jobs", "documents")
    }
    internal = httpx.Client(
        base_url=controller.url + "/internal/v1",
        headers={"Authorization": "Bearer " + controller_token},
        timeout=30,
    )
    resources = {}
    for role, served in workers.items():
        created = internal.post(
            "/resources",
            json={"name": f"test-{role}", "role": role, "url": served.url, "token": worker_token},
        )
        assert created.status_code == 201, created.text
        resources[role] = created.json()["id"]
    for resource_id in resources.values():
        wait_ready(internal, resource_id)
    settings = Settings(
        **base,
        controller_url=controller.url,
        controller_token=SecretStr(controller_token),
        ticket_private_key_file=private,
        trusted_issuers=[
            TrustedIssuer(issuer=AGENT_ISSUER, jwks_uri=identity.agent_keys.uri),
            TrustedIssuer(
                issuer=AGENT_ISSUER_WRITABLE, jwks_uri=identity.agent_keys.uri, read_only=False
            ),
        ],
        **({"storage_sts_role_arn": seaweed.sts_role} if USE_VENDING else {}),
    )
    container = Container(settings, verifier=TokenVerifier(settings, jwks=identity.jwks))
    for resource_id in resources.values():
        container.store.grant("compute", resource_id, "group:account users", "USE_COMPUTE", "test")
    app = create_app(container)
    api = Served(app)
    yield Stack(
        api.url, controller.url, settings, worker_settings, identity, workers, resources, app
    )
    api.stop()
    for served in workers.values():
        served.stop()
    controller.stop()
    internal.close()


ADMIN = ("data-admins",)


# ------------------------------------------------------------------ documents and memory (no object storage)
@dataclass
class DocsService:
    """The API with the document and memory engines on local files: fast, needs no Docker."""

    url: str
    app: Any
    settings: Settings
    identity: Identity
    served: Served

    def agent_token(self, user: str, tenant: str = "acme", *scopes: str, **claims: Any) -> str:
        scopes = scopes or ("documents:write", "memory:write")
        return self.identity.agent_token(user, tenant, scopes=scopes, **claims)

    def client(self, user: str, tenant: str = "acme", *scopes: str, **claims: Any):
        from loamz.data.client import DataClient

        return DataClient(self.url, token=lambda: self.agent_token(user, tenant, *scopes, **claims))

    def admin(self, tenant: str = "acme"):
        from loamz.data.client import DataClient

        return DataClient(
            self.url, token=lambda: self.identity.token("alice", ("data-admins",), tenant_id=tenant)
        )

    def http(self, user: str, tenant: str = "acme", *scopes: str, **claims: Any) -> httpx.Client:
        token = self.agent_token(user, tenant, *scopes, **claims)
        return httpx.Client(
            base_url=self.url + "/api/v1", headers={"Authorization": "Bearer " + token}, timeout=60
        )


class WordEmbedder:
    """A bag-of-words 'meaning': texts that share words are close. Enough to test retrieval without a model."""

    model, dimensions, max_input_tokens = "word-embed", 64, 512

    def __init__(self) -> None:
        from loamz.data.storage.models import Modality

        self.modalities = frozenset({Modality.TEXT})

    async def embed(self, inputs, *, query: bool = False):
        import hashlib
        import re

        from loamz.data.storage.models import EmbeddingResult
        from loamz.data.storage.types import TextBlock

        vectors = []
        for item in inputs:
            text = (
                item
                if isinstance(item, str)
                else " ".join(b.text if isinstance(b, TextBlock) else "FIGURE" for b in item)
            )
            vector = [0.0] * 64
            for word in re.findall(r"[a-z0-9]+", text.lower()):
                vector[int(hashlib.sha1(word.encode()).hexdigest(), 16) % 64] += 1.0
            vectors.append(vector)
        return EmbeddingResult(embeddings=vectors, model=self.model)


class WordReranker:
    model = "word-rerank"

    async def rerank(self, query, passages):
        import re

        words = set(re.findall(r"[a-z]+", query.lower()))
        return [float(len(words & set(re.findall(r"[a-z]+", p.lower())))) for p in passages]


def serve_documents(
    identity: Identity,
    ticket_keys: tuple[Path, Path],
    root: Path,
    *,
    seaweed: Any = None,
    retrieval_url: str = "",
    **engine: Any,
) -> DocsService:
    """With `seaweed`, bundles live in the tenants' volumes (the production path); without, in local files."""
    from loamz.data.server.app import create_app
    from loamz.data.server.features.documents.api import Documents, documents
    from loamz.data.server.features.documents.engine import DocumentEngine
    from loamz.data.server.features.files.service import files_service
    from loamz.data.server.features.tables.iceberg import table_service
    from loamz.data.server.kernel.container import Container

    storage = (
        dict(
            s3_endpoint_url=seaweed.s3,
            s3_access_key_id=SecretStr(seaweed.access_key),
            s3_secret_access_key=SecretStr(seaweed.secret_key),
        )
        if seaweed
        else {}
    )
    settings = Settings(
        _env_file=None,
        **storage,
        oidc_issuer=ISSUER,
        metadata=root / "catalog.sqlite",
        retrieval_dir=root / "retrieval",
        retrieval_url=SecretStr(retrieval_url),
        scratch=root / "tmp",
        controller_state=root / "compute",
        mask_salt=SecretStr("test-salt-docs"),
        rate_limit_per_minute=0,
        documents_parse_inline=True,
        ticket_private_key_file=ticket_keys[0],
        trusted_issuers=[
            TrustedIssuer(
                issuer=AGENT_ISSUER_WRITABLE, jwks_uri=identity.agent_keys.uri, read_only=False
            ),
            TrustedIssuer(issuer=AGENT_ISSUER, jwks_uri=identity.agent_keys.uri),
        ],
    )
    # No table service: the documents tests never touch Iceberg (it would dial the REST catalog at once).
    container = Container(settings, verifier=TokenVerifier(settings, jwks=identity.jwks))
    container.put(table_service, SimpleNamespace())
    if not seaweed:
        container.put(
            documents,
            Documents(
                DocumentEngine(settings, **engine), container.get(files_service), grants=False
            ),
        )
    app = create_app(container)
    served = Served(app)
    return DocsService(served.url, app, settings, identity, served)


@pytest.fixture(scope="module")
def docs(identity, ticket_keys, tmp_path_factory):
    service = serve_documents(
        identity,
        ticket_keys,
        tmp_path_factory.mktemp("docs"),
        embedder=WordEmbedder(),
        reranker=WordReranker(),
    )
    yield service
    service.served.stop()


# ------------------------------------------------------------------ separate OS processes (opt-in)
class ProcessStack:
    """Every service as its own process via the real CLI, like `make dev` + `make dev-compute`."""

    def __init__(self, seaweed: Seaweed, identity: Identity, keys: tuple[Path, Path], root: Path):
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(identity.private_key.public_key(), as_dict=True)
        body = (
            __import__("json")
            .dumps({"keys": [{**jwk, "kid": "test", "use": "sig", "alg": "RS256"}]})
            .encode()
        )

        class JWKS(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.jwks = ThreadingHTTPServer(("127.0.0.1", 0), JWKS)
        threading.Thread(target=self.jwks.serve_forever, daemon=True).start()
        self.identity, self.root = identity, root
        self.ports = {name: free_port() for name in ("api", "controller", "sql", "jobs")}
        self.worker_token = secrets.token_urlsafe(48)
        self.env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("LOAMZ_DATA_")},
            "LOAMZ_DATA_S3_ENDPOINT_URL": seaweed.s3,
            "LOAMZ_DATA_ICEBERG_CATALOG_URL": seaweed.iceberg,
            "LOAMZ_DATA_S3_ACCESS_KEY_ID": seaweed.access_key,
            "LOAMZ_DATA_S3_SECRET_ACCESS_KEY": seaweed.secret_key,
            "LOAMZ_DATA_OIDC_ISSUER": ISSUER,
            "LOAMZ_DATA_OIDC_JWKS_URL": f"http://127.0.0.1:{self.jwks.server_port}/certs",
            "LOAMZ_DATA_METADATA": str(root / "catalog.sqlite"),
            "LOAMZ_DATA_SCRATCH": str(root / "tmp"),
            "LOAMZ_DATA_CONTROLLER_STATE": str(root / "compute"),
            "LOAMZ_DATA_CONTROLLER_URL": f"http://127.0.0.1:{self.ports['controller']}",
            "LOAMZ_DATA_CONTROLLER_TOKEN": secrets.token_urlsafe(48),
            "LOAMZ_DATA_TICKET_PRIVATE_KEY_FILE": str(keys[0]),
            "LOAMZ_DATA_TICKET_PUBLIC_KEY_FILE": str(keys[1]),
        }
        self.processes: dict[str, subprocess.Popen] = {}

    def command(self, name: str) -> list[str]:
        cli = str(Path(__import__("sys").executable).parent / "loamz-data")
        if name in {"sql", "jobs"}:
            return [
                cli,
                "--env-file",
                "/dev/null",
                "worker",
                "--role",
                name,
                "--port",
                str(self.ports[name]),
            ]
        return [
            cli,
            "--env-file",
            "/dev/null",
            "serve" if name == "api" else "controller",
            "--port",
            str(self.ports[name]),
        ]

    def start(self, name: str) -> None:
        env = dict(self.env)
        if name in {"sql", "jobs"}:
            # Workers get their own token and the public key only.
            for key in (
                "LOAMZ_DATA_CONTROLLER_TOKEN",
                "LOAMZ_DATA_TICKET_PRIVATE_KEY_FILE",
            ):
                env.pop(key)
            env["LOAMZ_DATA_WORKER_TOKEN"] = self.worker_token
        log = open(self.root / f"{name}.log", "ab")
        self.processes[name] = subprocess.Popen(
            self.command(name), env=env, stdout=log, stderr=subprocess.STDOUT
        )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                if (
                    httpx.get(f"http://127.0.0.1:{self.ports[name]}/livez", timeout=1).status_code
                    == 200
                ):
                    return
            except httpx.HTTPError:
                pass
            if self.processes[name].poll() is not None:
                break
            time.sleep(0.2)
        raise AssertionError(f"{name} failed to start; see {self.root / (name + '.log')}")

    def kill(self, name: str) -> None:
        self.processes[name].kill()
        self.processes[name].wait(10)

    def stop(self) -> None:
        for process in self.processes.values():
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(10)
                except subprocess.TimeoutExpired:
                    process.kill()
        self.jwks.shutdown()

    def up(self) -> None:
        for name in ("controller", "sql", "jobs", "api"):
            self.start(name)
        internal = httpx.Client(
            base_url=self.env["LOAMZ_DATA_CONTROLLER_URL"] + "/internal/v1",
            headers={"Authorization": "Bearer " + self.env["LOAMZ_DATA_CONTROLLER_TOKEN"]},
        )
        for role in ("sql", "jobs"):
            created = internal.post(
                "/resources",
                json={
                    "name": role,
                    "role": role,
                    "url": f"http://127.0.0.1:{self.ports[role]}",
                    "token": self.worker_token,
                },
            ).json()
            wait_ready(internal, created["id"])
        internal.close()

    def client(self, name: str = "alice", *groups: str):
        from loamz.data.client import DataClient

        groups = groups or ADMIN
        return DataClient(
            f"http://127.0.0.1:{self.ports['api']}", token=lambda: self.identity.token(name, groups)
        )


@pytest.fixture
def process_stack(seaweed, identity, ticket_keys, tmp_path):
    if not os.getenv("LOAMZ_DATA_TEST_PROCESSES"):
        pytest.skip("Set LOAMZ_DATA_TEST_PROCESSES=1 for separate-process tests")
    stack = ProcessStack(seaweed, identity, ticket_keys, tmp_path)
    try:
        stack.up()
        yield stack
    finally:
        stack.stop()
