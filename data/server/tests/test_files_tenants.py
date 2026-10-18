"""Tenant provisioning: one idempotent call makes a tenant's catalog, schemas, volume and grants."""

from __future__ import annotations

import pytest
from conftest import ADMIN

from loamz.data.client import (
    ConflictError,
    DataError,
    ForbiddenError,
    InsufficientScopeError,
    NotFoundError,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def admin(stack):
    return stack.client("alice", *ADMIN)


@pytest.fixture(scope="module", autouse=True)
def unthrottled(stack):
    limit, stack.settings.rate_limit_per_minute = stack.settings.rate_limit_per_minute, 0
    yield
    stack.settings.rate_limit_per_minute = limit


def test_provisioning_creates_the_tenant_once_and_is_idempotent(admin):
    first = admin.tenants.ensure("t-one", "one")
    assert first == {
        "tenant_id": "t-one",
        "alias": "one",
        "catalog": "tenant_one",
        "schemas": ["tenant_one.workspace", "tenant_one.knowledge"],
        "volumes": ["tenant_one.workspace.files"],
        "group": "group:org:t-one",
        "created": True,
    }
    again = admin.tenants.ensure("t-one", "one")
    assert again == {**first, "created": False}
    assert [s["name"] for s in admin.catalog.schemas("tenant_one")] == ["knowledge", "workspace"]
    assert [v["name"] for v in admin.catalog.volumes("tenant_one", "workspace")] == ["files"]
    grants = {
        (a["principal"], p)
        for a in admin.catalog.grants("volume", "tenant_one.workspace.files")
        for p in a["privileges"]
    }
    assert grants == {("group:org:t-one", "READ_VOLUME"), ("group:org:t-one", "WRITE_VOLUME")}


def test_an_existing_tenant_is_completed_not_duplicated(stack, admin):
    # A half-provisioned tenant (the catalog exists, nothing else) is finished by the next call.
    with stack.http("alice", *ADMIN) as http:
        made = http.post("/catalogs", json={"name": "tenant_half", "comment": "tenant:t-half"})
        assert made.status_code == 201
    done = admin.tenants.ensure("t-half", "half")
    assert done["created"] is True and done["volumes"] == ["tenant_half.workspace.files"]
    assert admin.tenants.ensure("t-half", "half")["created"] is False


def test_an_alias_cannot_be_taken_over_by_another_tenant(admin):
    admin.tenants.ensure("t-owner", "owned")
    with pytest.raises(ConflictError) as taken:
        admin.tenants.ensure("t-intruder", "owned")
    assert taken.value.code == "conflict"
    for body in (
        {"tenant_id": "t", "alias": "Bad-Alias"},
        {"tenant_id": "t", "alias": "x" * 60},
        {"tenant_id": "", "alias": "ok"},
        {"tenant_id": "t", "alias": "ok", "extra": 1},
    ):
        with pytest.raises(DataError) as invalid:
            admin.request("POST", "/tenants", json=body)
        assert invalid.value.status == 400


def test_only_admins_and_provisioners_may_provision(stack):
    with pytest.raises(ForbiddenError):
        stack.client("bob", "analysts").tenants.ensure("t-no", "no")
    with pytest.raises(ForbiddenError):
        stack.client("carol", "org:t-no").tenants.ensure("t-no", "no")  # a tenant's own member
    provisioner = stack.client("platform", "data-provisioners")
    assert provisioner.tenants.ensure("t-prov", "prov")["created"] is True
    # Delegated tokens never provision: the route declares no scope.
    agent = stack.agent_client("u1", "acme", scopes=("files:write",), groups=["data-provisioners"])
    with pytest.raises(InsufficientScopeError):
        agent.tenants.ensure("t-agent", "agent")
    admin = stack.client("alice", *ADMIN)
    for refused in ("tenant_no", "tenant_agent"):
        with pytest.raises(NotFoundError):
            admin.request("GET", f"/catalogs/{refused}")


def test_members_use_their_tenants_workspace_and_nobody_elses(stack, admin):
    admin.tenants.ensure("t-a", "alpha")
    admin.tenants.ensure("t-b", "beta")
    member_a = stack.client("ann", "org:t-a")
    member_b = stack.client("ben", "org:t-b")
    volume = "tenant_alpha.workspace.files"
    info = member_a.files.upload(volume, "users/ann/plan.txt", b"alpha plan")
    assert info.version == 1 and member_a.files.read(volume, "users/ann/plan.txt") == b"alpha plan"
    assert [c["name"] for c in member_a.catalog.catalogs() if c["name"].startswith("tenant_")] == [
        "tenant_alpha"
    ]
    with pytest.raises(NotFoundError):
        member_b.files.read(volume, "users/ann/plan.txt")  # not even visible
    with pytest.raises(NotFoundError):
        member_b.files.upload(volume, "x.txt", b"x")
    # An agent acting for a member of tenant A reaches A's files, but only its own user's folder.
    agent = stack.agent_client("ann-sub", "alpha", scopes=("files:write",), groups=["org:t-a"])
    agent.files.upload(volume, "users/ann-sub/notes.txt", b"agent notes")
    with pytest.raises(ForbiddenError):
        agent.files.read(volume, "users/ann/plan.txt")
    shared = member_a.files.upload(volume, "shared/team.txt", b"team")
    assert agent.files.info(volume, "shared/team.txt").etag == shared.etag


def test_the_platforms_service_account_provisions_with_its_realm_role(stack):
    """cell-admin holds the client role `provision-tenant` on substrate-data (realm-substrate.json), not a group."""
    from loamz.data.client import DataClient

    role = {"resource_access": {"loamz-data": {"roles": ["provision-tenant"]}}}
    platform = DataClient(stack.api, token=lambda: stack.identity.token("cell-admin", **role))
    assert platform.tenants.ensure("t-role", "viarole")["created"] is True
    other = {"resource_access": {"some-other-client": {"roles": ["provision-tenant"]}}}
    elsewhere = DataClient(stack.api, token=lambda: stack.identity.token("x", **other))
    with pytest.raises(ForbiddenError):
        elsewhere.tenants.ensure("t-other", "other")
