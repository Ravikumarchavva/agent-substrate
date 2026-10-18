"""A real substrate-data (its API in this process, throwaway SeaweedFS in Docker) for the ``DataFileStore`` tests.

The data service's own test harness (``data/substrate-data/tests/conftest.py``) is loaded by path rather than copied, so the
agent side is held to the same server, the same object store and the same token shapes the data tests use. Without Docker, or
without the data packages installed (``uv sync --all-packages``), every test here skips. Marked ``integration``: a plain
``pytest -m "not integration"`` leaves them out.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("substrate_data")
pytest.importorskip("substrate_data_client")

_REPO = Path(__file__).resolve().parents[5]
_HARNESS = _REPO / "data" / "substrate-data" / "tests" / "conftest.py"
if (
    not _HARNESS.exists()
):  # pragma: no cover - a source distribution without the monorepo
    pytest.skip(
        "the substrate-data test harness is not next to this package",
        allow_module_level=True,
    )

_spec = importlib.util.spec_from_file_location("substrate_data_test_harness", _HARNESS)
assert _spec and _spec.loader
harness: Any = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = harness
_spec.loader.exec_module(harness)

# Session fixtures of the harness, re-exported so pytest finds them here.
identity = harness.identity
ticket_keys = harness.ticket_keys
seaweed = harness.seaweed

TENANTS = ("t1", "t2")


class DataService:
    """The running API plus what tests need to talk to it."""

    def __init__(self, url: str, identity: Any, settings: Any) -> None:
        self.url, self.identity, self.settings = url, identity, settings

    def admin(self):
        from substrate_data_client import DataClient

        return DataClient(
            self.url, token=lambda: self.identity.token("alice", ("data-admins",))
        )

    def token_for(
        self,
        user: str,
        tenants: tuple[str, ...] = ("t1",),
        scopes: tuple[str, ...] = ("files:write",),
    ) -> str:
        """What the platform would mint for one run: delegated (``act``), one subject, groups for each tenant."""
        groups = [g for t in tenants for g in (f"tenant-{t}", f"org:{t}")]
        return self.identity.agent_token(user, tenants[0], scopes=scopes, groups=groups)


@pytest.fixture(scope="session")
def data_service(
    seaweed, identity, ticket_keys, tmp_path_factory
) -> Iterator[DataService]:
    from pydantic import SecretStr

    from substrate_data.api.app import build_services, create_app
    from substrate_data.auth.oidc import TokenVerifier
    from substrate_data.core.models import Settings, TrustedIssuer

    root = tmp_path_factory.mktemp("data-service")
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
        mask_salt=SecretStr("test-salt-agent-files"),
        rate_limit_per_minute=0,
        ticket_private_key_file=ticket_keys[0],
        trusted_issuers=[
            TrustedIssuer(
                issuer=harness.AGENT_ISSUER_WRITABLE,
                jwks_uri=identity.agent_keys.uri,
                read_only=False,
            ),
        ],
    )
    services = build_services(
        settings, verifier=TokenVerifier(settings, jwks=identity.jwks)
    )
    served = harness.Served(create_app(settings, services))
    service = DataService(served.url, identity, settings)
    with service.admin() as admin:
        for tenant in TENANTS:
            admin.tenants.ensure(tenant, tenant)
    yield service
    served.stop()
