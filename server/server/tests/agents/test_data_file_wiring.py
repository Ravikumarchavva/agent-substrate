"""With a data service configured the container keeps files in Loamz Data, and serves the key that lets it verify them."""

import httpx

from loamz.engine.agents.data import DataFileStore
from loamz.engine.agents.providers.files import LocalFileStore
from loamz.server.agents.bootstrap import Container, Settings
from loamz.server.agents.http import create_app


def test_files_are_local_without_a_data_service(tmp_path):
    container = Container(Settings(files={"path": str(tmp_path)}))
    assert isinstance(container.file_store, LocalFileStore)
    assert container.data_tokens is None and container.data_connection is None


def test_files_go_to_loamz_data_when_its_url_is_set(tmp_path):
    container = Container(
        Settings(
            files={"path": str(tmp_path / "files")},
            data={"url": "http://data-api:8020", "token_key_file": str(tmp_path / "k.pem")},
        )
    )
    assert isinstance(container.file_store, DataFileStore)
    assert container.file_store is container.file_store


async def test_the_public_key_is_served_for_the_data_service_to_verify_tokens(tmp_path):
    container = Container(
        Settings(
            database={"path": str(tmp_path / "a.db")},
            data={"url": "http://data-api:8020", "token_key_file": str(tmp_path / "k.pem")},
        )
    )
    app = create_app(container)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as client:
            keys = (await client.get("/.well-known/data-token-jwks.json")).json()["keys"]
    assert keys and keys[0]["kty"] == "OKP" and "d" not in keys[0]


def test_a_minted_token_carries_every_claim_the_data_service_requires(tmp_path):
    import jwt

    container = Container(
        Settings(data={"url": "http://data-api:8020", "token_key_file": str(tmp_path / "k.pem")})
    )
    minter = container.data_tokens
    token = minter.mint("acme", "alice", scopes={"files:read", "files:write"})
    claims = jwt.decode(
        token,
        jwt.PyJWK(minter.jwks()["keys"][0]).key,
        algorithms=["EdDSA"],
        audience="loamz-data",
        issuer="loamz-agents-files",
    )
    assert {"exp", "iat", "iss", "aud", "sub", "jti"} <= set(claims)
    assert claims["tenant_id"] == "acme" and claims["sub"] == "alice"


def test_times_from_the_data_service_become_epoch_seconds():
    from datetime import UTC, datetime

    from loamz.engine.agents.data.files import _epoch

    assert _epoch(None) == 0.0
    assert _epoch("2026-10-22T10:00:00+00:00") == datetime(2026, 10, 22, 10, tzinfo=UTC).timestamp()
    assert _epoch(datetime(2026, 10, 22, 10, tzinfo=UTC)) == _epoch("2026-10-22T10:00:00Z")
