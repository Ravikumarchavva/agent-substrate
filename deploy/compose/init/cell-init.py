"""One-shot cell bootstrap for the compose bundle. Idempotent: existing files are left alone.

Runs inside the substrate-data image (it has `cryptography`) as root, once, before the stateful services start.
  /out/seaweed/s3.json        SeaweedFS S3 identities: least privilege per service (same shape as data bootstrap)
  /out/ticket-private/ticket.pem   Ed25519 ticket signing key (control plane only)
  /out/ticket-public/ticket.pub.pem   its public half (workers)
  /out/postgres-tls/server.{crt,key}  self-signed server certificate, so database connections are encrypted
  /out/internal-tls/{ca.crt,server.crt,server.key}  private CA and one certificate for the controller and the workers:
                              substrate-data refuses plain HTTP between them off loopback, so they serve HTTPS
Secrets come from the environment (.env); nothing here invents a credential.
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.x509.oid import NameOID

OUT = Path(os.environ.get("OUT", "/out"))


def need(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"{name} is not set; run deploy/compose/gen-env.sh or fill .env")
    return value


def write(path: Path, data: bytes | str, mode: int, owner: tuple[int, int] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    path.chmod(mode)
    if owner:
        os.chown(path, *owner)
        os.chown(path.parent, *owner)


def s3_identities() -> None:
    warehouse, staging, volumes = (
        need("WAREHOUSE_BUCKET"),
        need("STAGING_BUCKET"),
        need("VOLUMES_BUCKET"),
    )
    rwl = ("Read", "Write", "List")
    body = {
        "identities": [
            {
                # Control plane: commits table metadata, signs upload URLs, and alone holds the volumes bucket.
                "name": "substrate-control",
                "credentials": [
                    {"accessKey": need("CONTROL_S3_ACCESS_KEY_ID"), "secretKey": need("CONTROL_S3_SECRET_ACCESS_KEY")}
                ],
                "actions": [
                    f"s3tables:*:{warehouse}",
                    *(f"{a}:{warehouse}" for a in rwl),
                    *(f"{a}:{staging}" for a in rwl),
                    *(f"{a}:{volumes}" for a in rwl),
                ],
            },
            {
                # Attached compute: reads tables, writes data files, reads staged uploads. Nothing on volumes.
                "name": "substrate-worker",
                "credentials": [
                    {"accessKey": need("WORKER_S3_ACCESS_KEY_ID"), "secretKey": need("WORKER_S3_SECRET_ACCESS_KEY")}
                ],
                "actions": [
                    *(f"{a}:{warehouse}" for a in rwl),
                    *(f"{a}:{staging}" for a in ("Read", "List")),
                ],
            },
            {
                # The agent server's own file store bucket (FILE_STORE_*), separate from governed data.
                "name": "substrate-agents",
                "credentials": [
                    {"accessKey": need("AGENTS_S3_ACCESS_KEY_ID"), "secretKey": need("AGENTS_S3_SECRET_ACCESS_KEY")}
                ],
                "actions": [f"{a}:{need('AGENTS_BUCKET')}" for a in rwl],
            },
        ]
    }
    # Always rewritten: it is derived from .env, which is the source of truth.
    write(OUT / "seaweed" / "s3.json", json.dumps(body, indent=2), 0o644)


def ticket_keys() -> None:
    private, public = OUT / "ticket-private" / "ticket.pem", OUT / "ticket-public" / "ticket.pub.pem"
    if private.exists() and public.exists():
        return
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    # uid 10001 is the unprivileged user of the substrate-data image.
    write(private, pem, 0o400, (10001, 10001))
    write(
        public,
        key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo),
        0o444,
    )


def postgres_tls() -> None:
    crt, keyfile = OUT / "postgres-tls" / "server.crt", OUT / "postgres-tls" / "server.key"
    if crt.exists() and keyfile.exists():
        return
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "postgres")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("postgres")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    # uid 999 is the postgres user of the official image; the server insists the key is 0600 and its own.
    write(keyfile, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()), 0o600, (999, 999))
    write(crt, cert.public_bytes(serialization.Encoding.PEM), 0o644, (999, 999))


def internal_tls() -> None:
    d = OUT / "internal-tls"
    if (d / "ca.crt").exists() and (d / "server.crt").exists():
        return
    now = datetime.datetime.now(datetime.UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "substrate cell internal CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(True, False, False, False, False, True, True, False, False), critical=True
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    hosts = ["data-controller", "data-worker-sql", "data-worker-jobs", "data-worker-rag", "localhost"]
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "substrate-data internal")]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(h) for h in hosts]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(True, False, False, False, False, False, False, False, False), critical=True
        )
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    owner = (10001, 10001)
    write(d / "ca.crt", ca.public_bytes(serialization.Encoding.PEM), 0o444, owner)
    write(d / "server.crt", cert.public_bytes(serialization.Encoding.PEM), 0o444, owner)
    write(
        d / "server.key",
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()),
        0o400,
        owner,
    )


if __name__ == "__main__":
    s3_identities()
    internal_tls()
    ticket_keys()
    postgres_tls()
    print("cell-init: done")
