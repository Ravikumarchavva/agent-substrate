"""Documents on the tenant's volume (real object storage, throwaway SeaweedFS): the OKF bundle is the source of truth, erasure reaches it."""

from __future__ import annotations

import boto3
import pytest
from botocore.client import Config

from loamz.core.exceptions import ForbiddenError, NotFoundError
from loamz.engine.testing.conformance.document_library import prose
from loamz.server.data.features.documents.api import documents
from tests.data.conftest import VOLUMES_BUCKET, serve_documents
from tests.data.test_documents import extraction

pytestmark = pytest.mark.integration
VOLUME = "tenant_vt.workspace.files"


@pytest.fixture(scope="module")
def vol(seaweed, identity, ticket_keys, tmp_path_factory):
    service = serve_documents(
        identity, ticket_keys, tmp_path_factory.mktemp("vol"), seaweed=seaweed
    )
    with service.admin("vt") as admin:
        admin.tenants.ensure("vt")
    s3 = boto3.client(
        "s3",
        endpoint_url=seaweed.s3,
        aws_access_key_id=seaweed.access_key,
        aws_secret_access_key=seaweed.secret_key,
        region_name="us-east-1",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    yield service, s3
    service.served.stop()


def objects(s3) -> int:
    pages = s3.get_paginator("list_objects_v2").paginate(Bucket=VOLUMES_BUCKET)
    return sum(len(p.get("Contents", [])) for p in pages)


def test_the_bundle_is_files_in_the_volume_and_reindex_rebuilds_the_index(vol):
    service, s3 = vol
    name = "tenants/vt/users/alice/conversations/c1/documents"
    with service.client("alice", "vt") as alice:
        cid = alice.documents.ensure(name)["id"]
        added = alice.documents.add(
            cid, "plan.pdf", extraction=extraction(prose("harbour"), title="Plan")
        )
    with service.client("alice", "vt", "documents:write", "files:write") as alice:
        listed = [
            f.path for f in alice.files.list(VOLUME, "users/alice/conversations/c1/documents/")
        ]
        assert f"users/alice/conversations/c1/documents/{added['document']}/index.md" in listed
        assert "users/alice/conversations/c1/documents/index.md" in listed
        assert alice.documents.search(cid, "harbour committee")["hits"]
        # the index is derived: drop it and rebuild it from the bundle
        engine = service.app.state.container.get(documents).engine
    assert objects(s3) >= len(listed)
    with service.client("alice", "vt") as alice:
        assert alice.documents.reindex(cid) == 1
        assert alice.documents.search(cid, "harbour committee")["hits"]
    with service.client(
        "bob", "vt"
    ) as bob:  # the grants and the owner's folder apply to the bundle too
        with pytest.raises(ForbiddenError):
            bob.documents.search(cid, "harbour")
    assert engine is not None


def test_erasure_removes_files_versions_and_objects_and_the_audit_chain_verifies(vol):
    service, s3 = vol
    name = "tenants/vt/users/carol/conversations/c1/documents"
    with service.client("carol", "vt", "documents:write", "files:write") as carol:
        cid = carol.documents.ensure(name)["id"]
        carol.documents.add(cid, "a.pdf", extraction=extraction(prose("quarry"), title="A"))
        carol.files.upload(VOLUME, "users/carol/notes.txt", b"v1")
        carol.files.upload(VOLUME, "users/carol/notes.txt", b"v2 of the notes")
        before = objects(s3)
    with service.admin("vt") as admin:
        bundle = admin.exports.create("carol", tenant_id="vt")
        assert any(f["path"] == "users/carol/notes.txt" for f in bundle["files"])
        assert bundle["collections"] and bundle["collections"][0]["documents"]
        receipt = admin.erasure.request("carol", tenant_id="vt")
    removed = receipt["removed"]
    assert removed["files"]["files"] >= 3  # the notes and the bundle's files
    assert (
        removed["files"]["versions"] >= removed["files"]["files"] + 1
    )  # the notes had two versions
    assert removed["files"]["objects"] >= 1
    assert removed["libraries"]["documents"] == 1
    assert objects(s3) < before
    with service.client("carol", "vt", "documents:write", "files:write") as carol:
        with pytest.raises(NotFoundError):
            carol.files.info(VOLUME, "users/carol/notes.txt")
        with pytest.raises(NotFoundError):  # the collection itself is gone
            carol.documents.search(cid, "quarry")
    assert service.app.state.container.store.verify_audit()["ok"]
