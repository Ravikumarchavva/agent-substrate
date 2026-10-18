"""Process configuration shared by the control plane, controller and workers.

Each process reads only the fields it needs. Secrets come from the environment (or the
private development `.env` written by scripts/bootstrap.py), never from arguments.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def validate_service_url(url: str, insecure_hosts: tuple[str, ...] = ()) -> str:
    """Every address a service calls: HTTP(S) without embedded credentials; HTTPS off loopback."""
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Service URLs must be HTTP(S) without credentials, query or fragment")
    if parsed.scheme == "http" and parsed.hostname not in LOOPBACK | set(insecure_hosts):
        raise ValueError("Remote service connections require HTTPS")
    return url.rstrip("/")


class TrustedIssuer(BaseModel):
    """Another system whose signed tokens this service accepts (for example agent-substrate).

    It runs on its own machine: its public keys are fetched over HTTPS from `jwks_uri` (cached, and
    re-read when a token names a key not yet seen, so rotation needs no coordination). Tokens it
    issues are read-only unless configured otherwise, and never carry admin rights.
    """

    model_config = ConfigDict(extra="forbid")
    issuer: str = Field(min_length=1, max_length=300)
    audience: str = "substrate-data"
    jwks_uri: str
    # Private CA that signed the issuer's server certificate; the system trust store when unset.
    jwks_ca_file: Path | None = None
    algorithms: list[Literal["EdDSA", "RS256", "ES256"]] = Field(default_factory=lambda: ["EdDSA"])
    username_claim: str = "sub"
    group_claim: str = "groups"
    read_only: bool = True

    @field_validator("jwks_uri")
    @classmethod
    def https_off_loopback(cls, value: str) -> str:
        return validate_service_url(value)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SUBSTRATE_DATA_", env_file=".env", extra="ignore")

    # Control-plane state
    # SQLite file for development and single nodes. Set `metadata_url` (postgresql://…, with
    # sslmode=verify-full off this machine) to use PostgreSQL instead: several API replicas, HA.
    metadata: Path = Path(".workspace/catalog.sqlite")
    metadata_url: SecretStr = SecretStr("")
    metadata_pool_size: int = Field(default=16, ge=2, le=200)
    scratch: Path = Path(".workspace/tmp")

    # Object storage and the Iceberg REST catalog. The S3 key pair is this process's own identity.
    s3_endpoint_url: str = "http://127.0.0.1:18333"
    s3_region: str = "us-east-1"
    s3_access_key_id: SecretStr = SecretStr("")
    s3_secret_access_key: SecretStr = SecretStr("")
    iceberg_catalog_url: str = "http://127.0.0.1:18181"
    warehouse: str = Field(default="warehouse", pattern=r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")
    staging_bucket: str = Field(
        default="substrate-staging", pattern=r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$"
    )

    # Governed files (docs/FILES.md). User files live in their own bucket that compute workers cannot read
    # (the worker credentials carry no actions on it). `volumes_public_endpoint_url` is the address clients
    # use for presigned URLs when it differs from the control plane's own (a reverse proxy, a CDN).
    volumes_bucket: str = Field(
        default="substrate-volumes", pattern=r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$"
    )
    volumes_public_endpoint_url: str = ""
    files_part_bytes: int = Field(default=16 * 1024 * 1024, ge=5 * 1024 * 1024, le=5 * 1024**3)
    files_url_ttl_seconds: int = Field(default=900, ge=30, le=7 * 24 * 3600)
    files_max_file_bytes: int = Field(default=5 * 1024**4, ge=1024)
    # Pending uploads and upload sessions expire after this many hours; a sweeper removes their objects.
    upload_session_ttl_hours: int = Field(default=24, ge=1, le=24 * 14)
    # Versions that are no longer current (and files deleted this long ago) are purged. 0 keeps them.
    files_version_retention_days: int = Field(default=30, ge=0, le=3650)
    files_sweep_interval_seconds: float = Field(default=900, gt=0)
    # Members of this group may provision tenants (POST /api/v1/tenants), besides metastore admins.
    provisioner_group: str = "data-provisioners"

    # Identity
    oidc_issuer: str = "http://127.0.0.1:18080/realms/substrate"
    oidc_audience: str = "substrate-data"
    oidc_jwks_url: str = ""
    admin_group: str = "data-admins"
    # Other token issuers to trust, as JSON in SUBSTRATE_DATA_TRUSTED_ISSUERS.
    trusted_issuers: list[TrustedIssuer] = Field(default_factory=list)

    # Transport security of the API listener. Certificates are files (mounted secrets, never arguments).
    # With a client CA, every caller must present a certificate it signed (mutual TLS).
    tls_cert_file: Path | None = None
    tls_key_file: Path | None = None
    tls_client_ca_file: Path | None = None
    tls_min_version: Literal["1.2", "1.3"] = "1.3"
    # How this process authenticates when it calls the controller or workers: the CA that signed their
    # certificates (system store if unset) and its own client certificate for mutual TLS. Run the
    # controller and workers with tls_cert_file/tls_key_file and tls_client_ca_file to require it.
    internal_ca_file: Path | None = None
    internal_cert_file: Path | None = None
    internal_key_file: Path | None = None
    # Declares that a trusted proxy or service mesh in front of this process terminates TLS. Without
    # it (or certificates), the API refuses to listen on anything but loopback.
    behind_tls_proxy: bool = False

    # Statement and audit history older than this is deleted (GDPR storage limitation); 0 keeps it.
    history_retention_days: int = Field(default=365, ge=0, le=3650)

    # Secret mixed into `hash` column masks so hashed low-entropy values (emails, ids) cannot be
    # reversed by guessing. Required to create a hash mask; changing it changes every hash.
    mask_salt: SecretStr = SecretStr("")

    # Query tickets (control plane signs, workers verify)
    ticket_private_key_file: Path | None = None
    ticket_public_key_file: Path | None = None
    ticket_ttl_seconds: int = Field(default=60, ge=5, le=300)

    # Controller and attached compute
    controller_url: str = "http://127.0.0.1:8014"
    controller_token: SecretStr = SecretStr("")
    controller_state: Path | None = None
    # PostgreSQL for the controller's job queue instead of a local file: run several controllers, one
    # active at a time with automatic takeover. Use a database separate from the catalog's. Off this
    # machine it must carry sslmode=verify-full (or verify-ca, require).
    controller_state_url: SecretStr = SecretStr("")
    worker_token: SecretStr = SecretStr("")
    worker_bind_host: str = "127.0.0.1"
    worker_concurrency: int = Field(default=4, ge=1, le=64)
    # None: DuckDB uses every core of the attached machine.
    worker_threads: int | None = Field(default=None, ge=1, le=512)
    worker_memory_limit: str = "4GB"
    # A batch job (ingest, optimize, materialize) that grows past this is stopped before it can take the
    # machine down. 0 = half the memory this worker may use (its container limit, else the host's).
    worker_job_memory_mb: int = Field(default=0, ge=0)
    # `optimize` with sort_by must hold its input in memory: larger inputs are refused up front and
    # should be sorted partition by partition (`where`). Compressed Parquet size, in MiB. Measured: a sort
    # needs about 30x its compressed input in memory (10.7M taxi rows, about 220 MB of Parquet by the table average, added about 7 GB).
    sort_max_input_mb: int = Field(default=256, ge=0)
    # Finished statements are remembered by plan hash (same SQL over the same files = same rows).
    # 0 disables. Only results up to `worker_result_cache_entry_mb` are kept.
    worker_result_cache_mb: int = Field(default=128, ge=0, le=8192)
    worker_result_cache_entry_mb: int = Field(default=8, ge=1, le=256)

    # Optional model endpoint for inference workers
    embedding_url: str = ""
    embedding_model: str = ""
    embedding_token: SecretStr = SecretStr("")

    # Credential vending (docs/OPERATIONS.md): when a role ARN is set, the control plane asks the object
    # store's STS for credentials limited to the tables each statement reads, and workers need no storage
    # key to run queries. Empty keeps the single worker key.
    storage_sts_role_arn: str = ""
    storage_sts_endpoint: str = ""  # default: the S3 endpoint
    storage_sts_session_seconds: int = Field(default=900, ge=900, le=3600)

    # Names the warehouse independent of how each process reaches it (a container sees the same
    # storage at a different address). Set the same value on the control plane, controller and
    # every worker; a worker with a different value is refused. Defaults to the endpoint address.
    storage_id: str = ""

    # Explicit opt-in for plain HTTP to named non-loopback hosts (e.g. host.docker.internal in dev).
    insecure_http_hosts: tuple[str, ...] = ()

    # Fair use, per authenticated principal. Requests per minute are counted per API replica; the daily
    # scan budget is kept in the catalog and shared by all replicas. 0 disables a limit.
    rate_limit_per_minute: int = Field(default=1200, ge=0)
    max_concurrent_statements: int = Field(default=16, ge=0)
    max_concurrent_statements_per_tenant: int = Field(default=64, ge=0)
    daily_scan_bytes_per_principal: int = Field(default=0, ge=0)

    # Limits
    max_rows: int = Field(default=1000, ge=1, le=100000)
    max_result_bytes: int = Field(default=256 * 1024 * 1024, ge=1024)
    statement_timeout_seconds: float = Field(default=60, gt=0, le=3600)
    upload_part_bytes: int = Field(default=64 * 1024 * 1024, ge=5 * 1024 * 1024)
    max_upload_bytes: int = Field(default=1024**4, ge=1024)

    @model_validator(mode="after")
    def normalize(self) -> Settings:
        if self.controller_state is None:
            self.controller_state = self.metadata.parent / "compute"
        allowed = self.insecure_http_hosts
        self.s3_endpoint_url = validate_service_url(self.s3_endpoint_url, allowed)
        self.iceberg_catalog_url = validate_service_url(self.iceberg_catalog_url, allowed)
        self.oidc_issuer = validate_service_url(self.oidc_issuer, allowed)
        self.controller_url = validate_service_url(self.controller_url, allowed)
        if self.volumes_public_endpoint_url:
            self.volumes_public_endpoint_url = validate_service_url(
                self.volumes_public_endpoint_url, allowed
            )
        if self.embedding_url:
            self.embedding_url = validate_service_url(self.embedding_url, allowed)
        if bool(self.tls_cert_file) != bool(self.tls_key_file):
            raise ValueError("tls_cert_file and tls_key_file must be set together")
        if bool(self.internal_cert_file) != bool(self.internal_key_file):
            raise ValueError("internal_cert_file and internal_key_file must be set together")
        if self.tls_client_ca_file and not self.tls_cert_file:
            raise ValueError("tls_client_ca_file needs tls_cert_file and tls_key_file")
        return self

    @property
    def jwks_url(self) -> str:
        return self.oidc_jwks_url or self.oidc_issuer + "/protocol/openid-connect/certs"
