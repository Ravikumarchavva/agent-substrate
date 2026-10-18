"""Governed asset registration, grants and direct-to-S3 volume access."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from conftest import ISSUER
from substrate_data.api.app import create_app
from substrate_data.auth.oidc import TokenVerifier
from substrate_data.core.models import Settings
from substrate_data.tables.staging import Staging

BASE = "/api/v1/catalogs/main/schemas/default"


@pytest.fixture
def asset_api(make_store, identity):
    settings = Settings(
        _env_file=None,
        oidc_issuer=ISSUER,
        controller_token=SecretStr("x" * 48),
        s3_access_key_id=SecretStr("test"),
        s3_secret_access_key=SecretStr("test-secret"),
    )
    store = make_store()
    store.ensure_default("data-admins")
    svc = SimpleNamespace(
        settings=settings,
        store=store,
        staging=Staging(settings),
        verifier=TokenVerifier(settings, jwks=identity.jwks),
    )
    # No lifespan: these control-plane routes do not require compute or Iceberg.
    client = TestClient(create_app(settings, svc))
    client.headers["Authorization"] = "Bearer " + identity.token("alice", ("data-admins",))
    yield client, svc, identity
    client.close()


def create(client, kind="volumes", name="documents", **extra):
    response = client.post(f"{BASE}/{kind}", json={"name": name, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def test_assets_are_real_and_do_not_shadow_views(asset_api):
    client, svc, _ = asset_api
    volume = create(client)
    model = create(
        client,
        "models",
        "embedder",
        artifact_uri="s3://models/embedder/v1/",
        framework="transformers",
    )
    assert volume["version"] == 1
    assert volume["properties"]["prefix"].startswith("volumes/")
    assert model["properties"]["model_version"] == 1
    assert len(client.get(f"{BASE}/volumes").json()["volumes"]) == 1
    assert len(client.get(f"{BASE}/models").json()["models"]) == 1
    # The generic asset collection route must not intercept the existing views route.
    assert client.get(f"{BASE}/views").json()["views"] == []
    assert svc.store.asset("volume", "main", "default", "documents")["id"] == volume["id"]
    assert client.post(f"{BASE}/volumes", json={"name": "../invalid"}).status_code == 400
    assert client.post(f"{BASE}/models", json={"name": "missing_uri"}).status_code == 400


def test_assets_need_use_and_kind_specific_grants(asset_api):
    client, svc, identity = asset_api
    volume = create(client)
    model = create(client, "models", "embedder", artifact_uri="https://models.example/embedder")
    schema = svc.store.schema("main", "default")
    # SELECT on a schema must not make model or volume objects visible.
    svc.store.grant("schema", schema["id"], "user:bob", "SELECT", "user:alice")
    client.headers["Authorization"] = "Bearer " + identity.token("bob")
    assert client.get(f"{BASE}/volumes").json()["volumes"] == []
    assert client.get(f"{BASE}/models").json()["models"] == []
    assert client.post(f"{BASE}/volumes", json={"name": "denied"}).status_code == 403
    svc.store.grant("volume", volume["id"], "user:bob", "READ_VOLUME", "user:alice")
    assert client.get(f"{BASE}/volumes/documents").status_code == 200
    assert (
        client.post(
            f"{BASE}/volumes/documents/access", json={"path": "x", "action": "upload"}
        ).status_code
        == 403
    )
    assert client.delete(f"{BASE}/volumes/documents").status_code == 403
    svc.store.grant("model", model["id"], "user:bob", "USE_MODEL", "user:alice")
    assert client.get(f"{BASE}/models/embedder").status_code == 200
    # Owning the object does not bypass USE_SCHEMA on a private parent.
    private = svc.store.create_schema(svc.store.catalog("main"), "private", "user:alice", None)
    svc.store.create_asset(
        "volume", private, {"name": "secret", "owner": "user:bob", "properties": {}}
    )
    assert client.get("/api/v1/catalogs/main/schemas/private/volumes/secret").status_code == 404


@pytest.mark.parametrize(
    "path", ["../other", "folder/../other", "/absolute", "folder\\other", "folder/./x", "a\x00b"]
)
def test_volume_access_rejects_prefix_escapes(asset_api, path):
    client, _, _ = asset_api
    create(client)
    response = client.post(
        f"{BASE}/volumes/documents/access", json={"path": path, "action": "download"}
    )
    assert response.status_code == 400


@pytest.mark.parametrize(
    "uri",
    [
        "file:///etc/passwd",
        "https://u:secret@models.example/m",
        "https://models.example/m?token=secret",
        "javascript:alert(1)",
    ],
)
def test_model_registration_rejects_credential_uris(asset_api, uri):
    client, _, _ = asset_api
    response = client.post(f"{BASE}/models", json={"name": "embedder", "artifact_uri": uri})
    assert response.status_code == 400


@pytest.mark.integration
def test_volume_bytes_go_directly_to_seaweedfs(asset_api, seaweed):
    client, svc, _ = asset_api
    settings = svc.settings.model_copy(
        update={
            "s3_endpoint_url": seaweed.s3,
            "s3_access_key_id": SecretStr(seaweed.access_key),
            "s3_secret_access_key": SecretStr(seaweed.secret_key),
            "staging_bucket": "volume-assets-test",
        }
    )
    svc.settings = settings
    svc.staging = Staging(settings)
    svc.staging.s3.create_bucket(Bucket=settings.staging_bucket)
    volume = create(client)
    create(client, name="another_volume")
    content = b"volume bytes bypass the control plane"
    access = client.post(
        f"{BASE}/volumes/documents/access",
        json={"path": "docs/readme.txt", "action": "upload", "size_bytes": len(content)},
    ).json()
    assert httpx.put(access["url"], content=content).is_success
    directories = client.get(f"{BASE}/volumes/documents/files").json()["directories"]
    assert directories == [{"path": "docs", "directory": True}]
    files = client.get(f"{BASE}/volumes/documents/files", params={"path": "docs"}).json()["files"]
    assert files[0]["size_bytes"] == len(content)
    assert client.get(f"{BASE}/volumes/another_volume/files").json()["files"] == []
    access = client.post(
        f"{BASE}/volumes/documents/access", json={"path": "docs/readme.txt", "action": "download"}
    ).json()
    assert httpx.get(access["url"]).content == content
    assert client.delete(f"{BASE}/volumes/documents").status_code == 204
    assert client.get(f"{BASE}/volumes/documents").status_code == 404
    assert svc.staging.s3.head_object(
        Bucket=settings.staging_bucket, Key=volume["properties"]["prefix"] + "docs/readme.txt"
    )["ContentLength"] == len(content)


def test_asset_grant_api_and_non_empty_schema_guard(asset_api):
    client, svc, identity = asset_api
    volume = create(client)
    create(client, "models", "embedder", artifact_uri="s3://models/embedder/")
    for kind, name, privilege in [
        ("volume", "documents", "READ_VOLUME"),
        ("model", "embedder", "USE_MODEL"),
    ]:
        response = client.patch(
            f"/api/v1/grants/{kind}/main.default.{name}",
            json={"changes": [{"principal": "user:bob", "add": [privilege]}]},
        )
        assert response.status_code == 200, response.text
        assert response.json()["privilege_assignments"][0]["privileges"] == [privilege]
    client.headers["Authorization"] = "Bearer " + identity.token("bob")
    assert client.get(f"{BASE}/volumes/documents").status_code == 200
    assert client.get(f"{BASE}/models/embedder").status_code == 200
    # An asset alone is sufficient to prevent deleting a non-empty schema.
    from substrate_data.core.errors import Problem

    private = svc.store.create_schema(svc.store.catalog("main"), "assets_only", "user:alice", None)
    svc.store.create_asset(
        "volume", private, {"name": "files", "owner": "user:alice", "properties": {}}
    )
    with pytest.raises(Problem) as caught:
        svc.store.delete_securable("schema", svc.store.schema("main", "assets_only"), None)
    assert caught.value.status == 409
    assert volume["id"]


def test_asset_pagination_never_exposes_hidden_names(asset_api):
    from substrate_data.api.services import decode_token

    client, svc, identity = asset_api
    for name in ["a_visible", "b_hidden", "c_visible", "d_hidden", "e_visible"]:
        asset = create(client, name=name)
        if name.endswith("visible"):
            svc.store.grant("volume", asset["id"], "user:bob", "READ_VOLUME", "user:alice")
    client.headers["Authorization"] = "Bearer " + identity.token("bob")
    first = client.get(f"{BASE}/volumes", params={"page_size": 1}).json()
    assert [item["name"] for item in first["volumes"]] == ["a_visible"]
    assert decode_token(first["next_page_token"]) == "a_visible"
    second = client.get(
        f"{BASE}/volumes", params={"page_size": 1, "page_token": first["next_page_token"]}
    ).json()
    assert [item["name"] for item in second["volumes"]] == ["c_visible"]
    last = client.get(
        f"{BASE}/volumes", params={"page_size": 1, "page_token": second["next_page_token"]}
    ).json()
    assert [item["name"] for item in last["volumes"]] == ["e_visible"]
    assert last["next_page_token"] is None
