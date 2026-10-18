"""Create the private development configuration. Idempotent; never prints a secret.

Writes (all ignored by git, owner-only permissions):
  .env                                  every development secret and endpoint
  .workspace/infra/s3.json              SeaweedFS identities (least privilege per service)
  .workspace/infra/substrate-realm.json Keycloak realm: users, groups, clients
  .workspace/keys/ticket*.pem           Ed25519 key pair signing query tickets
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
INFRA = ROOT / ".workspace" / "infra"
KEYS = ROOT / ".workspace" / "keys"
UI_ORIGINS = ["http://localhost:3011", "http://127.0.0.1:3011"]

PUBLIC = {
    "SUBSTRATE_DATA_METADATA": ".workspace/control/catalog.sqlite",
    "SUBSTRATE_DATA_CONTROLLER_STATE": ".workspace/control/compute",
    "SUBSTRATE_DATA_SCRATCH": ".workspace/control/tmp",
    "SUBSTRATE_DATA_S3_ENDPOINT_URL": "http://127.0.0.1:18333",
    "SUBSTRATE_DATA_ICEBERG_CATALOG_URL": "http://127.0.0.1:18181",
    "SUBSTRATE_DATA_WAREHOUSE": "warehouse",
    "SUBSTRATE_DATA_STORAGE_ID": "dev-warehouse",
    "SUBSTRATE_DATA_STAGING_BUCKET": "substrate-staging",
    "SUBSTRATE_DATA_VOLUMES_BUCKET": "substrate-volumes",
    "SUBSTRATE_DATA_OIDC_ISSUER": "http://127.0.0.1:18080/realms/substrate",
    "SUBSTRATE_DATA_OIDC_AUDIENCE": "substrate-data",
    "SUBSTRATE_DATA_ADMIN_GROUP": "data-admins",
    "SUBSTRATE_DATA_TICKET_PRIVATE_KEY_FILE": ".workspace/keys/ticket.pem",
    "SUBSTRATE_DATA_TICKET_PUBLIC_KEY_FILE": ".workspace/keys/ticket.pub.pem",
    "SUBSTRATE_DATA_CONTROLLER_URL": "http://127.0.0.1:8014",
    "WEB_PUBLIC_URL": "http://127.0.0.1:3011",
}
SECRETS = {
    "SUBSTRATE_DATA_CONTROLLER_TOKEN": lambda: secrets.token_urlsafe(48),
    "CONTROL_S3_ACCESS_KEY_ID": lambda: "SDC" + secrets.token_hex(10).upper(),
    "CONTROL_S3_SECRET_ACCESS_KEY": lambda: secrets.token_urlsafe(32),
    "WORKER_S3_ACCESS_KEY_ID": lambda: "SDW" + secrets.token_hex(10).upper(),
    "WORKER_S3_SECRET_ACCESS_KEY": lambda: secrets.token_urlsafe(32),
    "WORKER_TOKEN": lambda: secrets.token_urlsafe(48),
    "KEYCLOAK_ADMIN_PASSWORD": lambda: secrets.token_urlsafe(18),
    "WEB_CLIENT_SECRET": lambda: secrets.token_urlsafe(32),
    "WEB_SESSION_SECRET": lambda: secrets.token_urlsafe(48),
    "BENCH_CLIENT_SECRET": lambda: secrets.token_urlsafe(32),
    "DEV_ALICE_PASSWORD": lambda: secrets.token_urlsafe(12),
    "DEV_BOB_PASSWORD": lambda: secrets.token_urlsafe(12),
}


def private_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(text)
    path.chmod(0o600)


def load_env() -> dict[str, str]:
    values = {}
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    return values


def environment() -> dict[str, str]:
    values = load_env()
    changed = False
    for key, value in PUBLIC.items():
        if key not in values:
            values[key], changed = value, True
    for key, make in SECRETS.items():
        if not values.get(key):
            values[key], changed = make(), True
    if changed:
        private_write(ENV, "".join(f"{key}={value}\n" for key, value in values.items()))
    return values


def seaweed_identities(env: dict[str, str]) -> None:
    warehouse, staging = env["SUBSTRATE_DATA_WAREHOUSE"], env["SUBSTRATE_DATA_STAGING_BUCKET"]
    volumes = env["SUBSTRATE_DATA_VOLUMES_BUCKET"]
    body = {
        "identities": [
            {
                # Control plane: commits table metadata and signs upload URLs. Never reads table data.
                # It alone holds the volumes bucket (user files): it signs URLs and copies objects there.
                "name": "substrate-control",
                "credentials": [
                    {
                        "accessKey": env["CONTROL_S3_ACCESS_KEY_ID"],
                        "secretKey": env["CONTROL_S3_SECRET_ACCESS_KEY"],
                    }
                ],
                "actions": [
                    f"s3tables:*:{warehouse}",
                    *(f"{a}:{warehouse}" for a in ("Read", "Write", "List")),
                    *(f"{a}:{staging}" for a in ("Read", "Write", "List")),
                    *(f"{a}:{volumes}" for a in ("Read", "Write", "List")),
                ],
            },
            {
                # Attached compute: reads tables, writes new data files, reads staged uploads.
                # No catalog actions, so it cannot commit or alter table metadata, and no action at all on
                # the volumes bucket: a compromised worker cannot read users' files.
                "name": "substrate-worker",
                "credentials": [
                    {
                        "accessKey": env["WORKER_S3_ACCESS_KEY_ID"],
                        "secretKey": env["WORKER_S3_SECRET_ACCESS_KEY"],
                    }
                ],
                "actions": [
                    *(f"{a}:{warehouse}" for a in ("Read", "Write", "List")),
                    *(f"{a}:{staging}" for a in ("Read", "List")),
                ],
            },
        ]
    }
    private_write(INFRA / "s3.json", json.dumps(body, indent=2))


def realm(env: dict[str, str]) -> None:
    audience = {
        "name": "substrate-data audience",
        "protocol": "openid-connect",
        "protocolMapper": "oidc-audience-mapper",
        "config": {
            "included.custom.audience": env["SUBSTRATE_DATA_OIDC_AUDIENCE"],
            "access.token.claim": "true",
            "id.token.claim": "false",
            "introspection.token.claim": "true",
        },
    }
    groups = {
        "name": "groups",
        "protocol": "openid-connect",
        "protocolMapper": "oidc-group-membership-mapper",
        "config": {
            "claim.name": "groups",
            "full.path": "false",
            "access.token.claim": "true",
            "id.token.claim": "true",
            "userinfo.token.claim": "true",
            "introspection.token.claim": "true",
        },
    }
    mappers = [audience, groups]
    body = {
        "realm": "substrate",
        "enabled": True,
        "sslRequired": "external",
        "accessTokenLifespan": 300,
        "ssoSessionIdleTimeout": 3600,
        "bruteForceProtected": True,
        "groups": [{"name": "data-admins"}, {"name": "analysts"}],
        "users": [
            {
                "username": "alice",
                "email": "alice@example.test",
                "firstName": "Alice",
                "lastName": "Admin",
                "enabled": True,
                "emailVerified": True,
                "credentials": [
                    {"type": "password", "value": env["DEV_ALICE_PASSWORD"], "temporary": False}
                ],
                "groups": ["/data-admins"],
            },
            {
                "username": "bob",
                "email": "bob@example.test",
                "firstName": "Bob",
                "lastName": "Analyst",
                "enabled": True,
                "emailVerified": True,
                "credentials": [
                    {"type": "password", "value": env["DEV_BOB_PASSWORD"], "temporary": False}
                ],
                "groups": ["/analysts"],
            },
            {
                "username": "service-account-substrate-data-bench",
                "enabled": True,
                "serviceAccountClientId": "substrate-data-bench",
                "groups": ["/data-admins"],
            },
        ],
        "clients": [
            {
                # The workspace UI's server side (backend-for-frontend): confidential + PKCE.
                "clientId": "substrate-data-web",
                "name": "Substrate Data workspace",
                "enabled": True,
                "publicClient": False,
                "secret": env["WEB_CLIENT_SECRET"],
                "standardFlowEnabled": True,
                "directAccessGrantsEnabled": False,
                "serviceAccountsEnabled": False,
                "redirectUris": [f"{origin}/auth/callback" for origin in UI_ORIGINS],
                "webOrigins": UI_ORIGINS,
                "attributes": {
                    "pkce.code.challenge.method": "S256",
                    "post.logout.redirect.uris": "##".join(f"{o}/*" for o in UI_ORIGINS),
                },
                "protocolMappers": mappers,
            },
            {
                # SDK / CLI sign-in for people, via the device authorization grant.
                "clientId": "substrate-data-cli",
                "name": "Substrate Data CLI and SDK",
                "enabled": True,
                "publicClient": True,
                "standardFlowEnabled": False,
                "directAccessGrantsEnabled": False,
                "attributes": {"oauth2.device.authorization.grant.enabled": "true"},
                "protocolMappers": mappers,
            },
            {
                # Non-interactive service principal for benchmarks and automation.
                "clientId": "substrate-data-bench",
                "name": "Substrate Data benchmarks",
                "enabled": True,
                "publicClient": False,
                "secret": env["BENCH_CLIENT_SECRET"],
                "standardFlowEnabled": False,
                "serviceAccountsEnabled": True,
                "protocolMappers": mappers,
            },
        ],
    }
    private_write(INFRA / "substrate-realm.json", json.dumps(body, indent=2))


def ticket_keys() -> None:
    private, public = KEYS / "ticket.pem", KEYS / "ticket.pub.pem"
    if private.exists() and public.exists():
        return
    key = Ed25519PrivateKey.generate()
    private_write(
        private,
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
    )
    public.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )


def main() -> None:
    env = environment()
    seaweed_identities(env)
    realm(env)
    ticket_keys()
    print("Development configuration ready in .env and .workspace/ (secrets not shown)")


if __name__ == "__main__":
    main()
