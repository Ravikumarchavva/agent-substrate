"""Tenant provisioning: one idempotent call makes a tenant's catalog, schemas, volume and grants.

The tenant's alias and every name are derived from its id (`loamz.core.tenancy`); its members reach its workspace
through the `org:<tenant>` group the server derives from their verified organization, never from a token's groups.
"""

from __future__ import annotations

import pytest

from loamz.core.exceptions import (
    ConflictError,
    ForbiddenError,
    HttpResponseError,
    InsufficientScopeError,
    NotFoundError,
)
from tests.data.conftest import ADMIN

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
    first = admin.tenants.ensure("t-one")
    assert first == {
        "tenant_id": "t-one",
        "alias": "t_one",
        "catalog": "tenant_t_one",
        "schemas": ["tenant_t_one.workspace", "tenant_t_one.knowledge"],
        "volumes": ["tenant_t_one.workspace.files"],
        "group": "group:org:t-one",
        "created": True,
    }
    again = admin.tenants.ensure("t-one")
    assert again == {**first, "created": False}
    assert [s["name"] for s in admin.catalog.schemas("tenant_t_one")] == ["knowledge", "workspace"]
    assert [v["name"] for v in admin.catalog.volumes("tenant_t_one", "workspace")] == ["files"]
    grants = {
        (a["principal"], p)
        for a in admin.catalog.grants("volume", "tenant_t_one.workspace.files")
        for p in a["privileges"]
    }
    assert grants == {("group:org:t-one", "READ_VOLUME"), ("group:org:t-one", "WRITE_VOLUME")}


def test_an_existing_tenant_is_completed_not_duplicated(stack, admin):
    # A half-provisioned tenant (the catalog exists, nothing else) is finished by the next call.
    with stack.http("alice", *ADMIN) as http:
        made = http.post("/catalogs", json={"name": "tenant_t_half", "comment": "tenant:t-half"})
        assert made.status_code == 201
    done = admin.tenants.ensure("t-half")
    assert done["created"] is True and done["volumes"] == ["tenant_t_half.workspace.files"]
    assert admin.tenants.ensure("t-half")["created"] is False


def test_a_catalog_name_cannot_be_taken_over_by_another_tenant(admin):
    admin.tenants.ensure("acme-corp")
    with pytest.raises(ConflictError) as taken:  # `acme_corp` has the same alias
        admin.tenants.ensure("acme_corp")
    assert taken.value.code == "conflict"
    for body in (
        {"tenant_id": ""},
        {"tenant_id": "../x"},
        {"tenant_id": "t", "alias": "chosen"},  # the alias is derived, never chosen
    ):
        with pytest.raises(HttpResponseError) as invalid:
            admin.request("POST", "/tenants", json=body)
        assert invalid.value.status == 422


def test_only_admins_and_the_provisioning_role_may_provision(stack):
    with pytest.raises(ForbiddenError):
        stack.client("bob", "analysts").tenants.ensure("t-no")
    with pytest.raises(ForbiddenError):
        stack.member("carol", "t-no").tenants.ensure("t-no")  # a tenant's own member
    with pytest.raises(ForbiddenError):  # a group is not the role
        stack.client("platform", "data-provisioners").tenants.ensure("t-prov")
    # Delegated tokens never provision: the route declares no scope.
    agent = stack.agent_client("u1", "acme", scopes=("files:write",))
    with pytest.raises(InsufficientScopeError):
        agent.tenants.ensure("t-agent")
    admin = stack.client("alice", *ADMIN)
    for refused in ("tenant_t_no", "tenant_t_prov", "tenant_t_agent"):
        with pytest.raises(NotFoundError):
            admin.request("GET", f"/catalogs/{refused}")


def test_members_use_their_tenants_workspace_and_nobody_elses(stack, admin):
    admin.tenants.ensure("t-a")
    admin.tenants.ensure("t-b")
    member_a = stack.member("ann", "t-a")
    member_b = stack.member("ben", "t-b")
    volume = "tenant_t_a.workspace.files"
    info = member_a.files.upload(volume, "users/ann/plan.txt", b"alpha plan")
    assert info.version == 1 and member_a.files.read(volume, "users/ann/plan.txt") == b"alpha plan"
    assert [c["name"] for c in member_a.catalog.catalogs() if c["name"].startswith("tenant_")] == [
        "tenant_t_a"
    ]
    with pytest.raises(NotFoundError):
        member_b.files.read(volume, "users/ann/plan.txt")  # not even visible
    with pytest.raises(NotFoundError):
        member_b.files.upload(volume, "x.txt", b"x")
    # A token naming the group itself gets nothing: the group comes from the verified organization only.
    with pytest.raises(NotFoundError):
        stack.client("eve", "org:t-a").files.read(volume, "users/ann/plan.txt")
    # An agent acting for a member of tenant A reaches A's files, but only its own user's folder.
    agent = stack.agent_client("ann-sub", "t-a", scopes=("files:write",))
    agent.files.upload(volume, "users/ann-sub/notes.txt", b"agent notes")
    with pytest.raises(ForbiddenError):
        agent.files.read(volume, "users/ann/plan.txt")
    shared = member_a.files.upload(volume, "shared/team.txt", b"team")
    assert agent.files.info(volume, "shared/team.txt").etag == shared.etag


def test_the_platforms_service_account_provisions_with_its_realm_role(stack):
    """cell-admin holds the client role `provision-tenant` on loamz-data, not a group."""
    from loamz.data import DataClient

    role = {"resource_access": {"loamz-data": {"roles": ["provision-tenant"]}}}
    platform = DataClient(stack.api, credential=lambda: stack.identity.token("cell-admin", **role))
    assert platform.tenants.ensure("t-role")["created"] is True
    other = {"resource_access": {"some-other-client": {"roles": ["provision-tenant"]}}}
    elsewhere = DataClient(stack.api, credential=lambda: stack.identity.token("x", **other))
    with pytest.raises(ForbiddenError):
        elsewhere.tenants.ensure("t-other")
