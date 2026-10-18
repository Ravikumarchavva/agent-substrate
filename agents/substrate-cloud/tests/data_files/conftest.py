"""The platform's file routes against a real substrate-data (its API in this process, throwaway SeaweedFS in Docker).

The data service's own harness (``data/substrate-data/tests/conftest.py``) is loaded by path so this side is held to the same server,
the same object store and the same token shapes. Tokens here come from the platform's real ``DataTokenMinter``, whose public key is
published for the data service exactly as the JWKS route does. Without Docker every test in this folder skips.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

pytest.importorskip("substrate_data")

_HARNESS = (
    Path(__file__).resolve().parents[4]
    / "data"
    / "substrate-data"
    / "tests"
    / "conftest.py"
)
if not _HARNESS.exists():  # pragma: no cover
    pytest.skip(
        "the substrate-data test harness is not next to this package",
        allow_module_level=True,
    )
_spec = importlib.util.spec_from_file_location(
    "substrate_data_test_harness_cloud", _HARNESS
)
assert _spec and _spec.loader
harness: Any = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = harness
_spec.loader.exec_module(harness)

identity = harness.identity
ticket_keys = harness.ticket_keys
seaweed = harness.seaweed

TENANT = "acme"
OTHER_TENANT = "globex"


class DataService:
    def __init__(self, url: str, identity: Any, key: Ed25519PrivateKey) -> None:
        self.url, self.identity, self.key = url, identity, key

    def admin(self):
        from substrate_data_client import DataClient

        return DataClient(
            self.url, token=lambda: self.identity.token("alice", ("data-admins",))
        )

    def minter(self):
        from substrate_cloud.data_access import DataTokenMinter

        return DataTokenMinter(
            self.key,
            issuer=harness.AGENT_ISSUER,
            audience="substrate-data",
            files_issuer=harness.AGENT_ISSUER_WRITABLE,
        )


@pytest.fixture(scope="session")
def data_service(
    seaweed, identity, ticket_keys, tmp_path_factory
) -> Iterator[DataService]:
    from pydantic import SecretStr

    from substrate_data.api.app import build_services, create_app
    from substrate_data.auth.oidc import TokenVerifier
    from substrate_data.core.models import Settings, TrustedIssuer

    key = Ed25519PrivateKey.generate()
    # The platform's key, trusted for writing files under the writable issuer name (what DATA_FILES_TOKEN_ISSUER names).
    from substrate_cloud.data_access import _kid

    identity.agent_keys.publish(_kid(key), key)
    root = tmp_path_factory.mktemp("data-service-cloud")
    settings = Settings(
        _env_file=None,
        s3_endpoint_url=seaweed.s3,
        iceberg_catalog_url=seaweed.iceberg,
        s3_access_key_id=SecretStr(seaweed.access_key),
        s3_secret_access_key=SecretStr(seaweed.secret_key),
        oidc_issuer=harness.ISSUER,
        metadata=root / "catalog.sqlite",
        scratch=root / "tmp",
        controller_state=root / "compute",
        mask_salt=SecretStr("test-salt-cloud-files"),
        rate_limit_per_minute=0,
        ticket_private_key_file=ticket_keys[0],
        trusted_issuers=[
            TrustedIssuer(
                issuer=harness.AGENT_ISSUER_WRITABLE,
                jwks_uri=identity.agent_keys.uri,
                read_only=False,
            )
        ],
    )
    services = build_services(
        settings, verifier=TokenVerifier(settings, jwks=identity.jwks)
    )
    served = harness.Served(create_app(settings, services))
    service = DataService(served.url, identity, key)
    with service.admin() as admin:
        for tenant in (TENANT, OTHER_TENANT):
            admin.tenants.ensure(tenant, tenant)
    yield service
    served.stop()
