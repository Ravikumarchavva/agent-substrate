"""The whole platform started with ``DATA_SERVICE_URL`` set: the database has no file tables and the process holds no object-store
credential, yet an upload round-trips through the data service."""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from substrate_cloud.monolith.app import app
from substrate_cloud.monolith.security.deps import get_current_user
from substrate_cloud.shared.auth.claims import AuthClaims
from substrate_cloud.shared.settings import settings

from .conftest import TENANT, DataService

pytestmark = [pytest.mark.integration, pytest.mark.requires_postgres]


async def test_with_a_data_service_the_agent_database_has_no_file_tables_and_the_server_holds_no_storage_credentials(
    data_service: DataService, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "DATA_SERVICE_URL", data_service.url)
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    # The platform signs with the key the data service already trusts (published by the fixture): its JWKS endpoint is not served here.
    key_file = tmp_path / "data-token.pem"
    key_file.write_bytes(
        data_service.key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    monkeypatch.setattr(settings, "DATA_TOKEN_KEY_FILE", str(key_file))
    from substrate_cloud import factory

    class _Everyone:
        async def is_member(self, tenant_id: str, user_id: str) -> bool:
            return True  # Keycloak membership is covered in test_data_access

    monkeypatch.setattr(factory, "_membership", lambda cfg: _Everyone())
    monkeypatch.setattr(settings, "DATA_TOKEN_ISSUER", "agent-substrate-test")
    monkeypatch.setattr(
        settings, "DATA_FILES_TOKEN_ISSUER", "agent-substrate-test-writable"
    )
    async with app.router.lifespan_context(app):
        ctx = app.state.ctx
        assert ctx.file_access is not None and ctx.file_access.data_backed
        assert type(ctx.file_store).__name__ == "KeyScopedFileStore"

        assert (
            app.state.data_minter.jwks()["keys"][0]["kid"]
            in data_service.identity.agent_keys.keys
        )

        # No table in the platform's database duplicates the Files domain.
        async with app.state.session_factory() as db:
            tables = {
                row[0]
                for row in await db.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
                    )
                )
            }
        assert {
            "files",
            "file_versions",
            "file_metadata",
            "workspace_quotas",
        } & tables == set()
        assert "threads" in tables  # the schema was really created

        # No object-store credential anywhere in the process's configuration or its file store.
        assert not [
            k for k in vars(settings) if "S3" in k.upper() or "BUCKET" in k.upper()
        ]
        assert not [
            a
            for a in vars(ctx.file_store)
            if "key" in a.lower() or "secret" in a.lower()
        ]

        # An upload still round-trips, through the data service.
        sub = "invariant-user"
        app.dependency_overrides[get_current_user] = lambda: AuthClaims(
            sub=sub, tenant_id=TENANT
        )
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test/api/v1"
            ) as client:
                up = await client.post(
                    "/files/upload",
                    files={
                        "file": ("n.txt", b"through the data service", "text/plain")
                    },
                )
                assert up.status_code == 201, up.text
                assert (
                    await client.get(f"/files/{up.json()['id']}/download")
                ).content == b"through the data service"
        finally:
            app.dependency_overrides.pop(get_current_user, None)
    with data_service.admin() as admin:
        assert (
            admin.files.read(
                "tenant_acme.workspace.files", f"users/{sub}/uploads/n.txt"
            )
            == b"through the data service"
        )
