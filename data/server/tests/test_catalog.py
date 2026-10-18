"""Unity Catalog-style securables: ownership, inherited privileges, USE on every ancestor."""

from __future__ import annotations

import pytest

from loamz.data.server.kernel.principal import Principal
from loamz.data.storage.errors import LoamzError, http_status


def user(name, *groups, admin=False):
    return Principal(subject=name, name=name, kind="user", groups=frozenset(groups), is_admin=admin)


@pytest.fixture
def store(make_store):
    store = make_store()
    store.ensure_default("data-admins")
    catalog = store.catalog("main")
    schema = store.create_schema(catalog, "sales", "group:data-admins", None)
    store.create_table(
        {**schema, "catalog_name": "main"}, {"name": "orders", "owner": "user:alice"}
    )
    return store


def test_default_namespace_is_browsable_not_readable(store):
    bob = user("bob", "analysts")
    assert store.can_browse(bob, "catalog", store.catalog("main"))
    assert store.can_browse(bob, "schema", store.schema("main", "default"))
    table = store.table("main", "sales", "orders")
    assert not store.can_browse(bob, "table", table)  # no USE_SCHEMA on sales
    with pytest.raises(LoamzError) as caught:
        store.require(bob, "table", table, "SELECT")
    assert http_status(caught.value) == 403


def test_privileges_inherit_downwards(store):
    bob = user("bob", "analysts")
    schema = store.schema("main", "sales")
    table = store.table("main", "sales", "orders")
    store.grant("schema", schema["id"], "group:analysts", "USE_SCHEMA", "admin")
    store.grant("schema", schema["id"], "group:analysts", "SELECT", "admin")
    store.require(bob, "table", table, "SELECT")
    with pytest.raises(LoamzError):
        store.require(bob, "table", table, "MODIFY")
    store.revoke("schema", schema["id"], "group:analysts", "SELECT")
    with pytest.raises(LoamzError):
        store.require(bob, "table", table, "SELECT")


def test_use_schema_on_a_table_does_not_open_the_schema(store):
    bob = user("bob")
    table = store.table("main", "sales", "orders")
    store.grant("table", table["id"], "user:bob", "SELECT", "admin")
    with pytest.raises(LoamzError, match="USE_SCHEMA"):
        store.require(bob, "table", table, "SELECT")


def test_owners_and_admins_hold_everything(store):
    table = store.table("main", "sales", "orders")
    alice = user("alice")
    # As in Unity Catalog, owning a table does not open its parents.
    with pytest.raises(LoamzError, match="USE_SCHEMA"):
        store.require(alice, "table", table, "SELECT")
    store.grant("schema", table["schema_id"], "user:alice", "USE_SCHEMA", "admin")
    store.require(alice, "table", table, "SELECT", "MODIFY")
    store.require(user("root", admin=True), "table", table, "MODIFY")


def test_grants_reject_privileges_that_do_not_apply(store):
    with pytest.raises(LoamzError):
        store.grant("table", "x", "user:bob", "CREATE_SCHEMA", "admin")


def test_optimistic_concurrency_and_non_empty_delete(store):
    schema = store.schema("main", "sales")
    with pytest.raises(LoamzError) as caught:
        store.update_securable("schema", schema, {"comment": "x"}, if_match=schema["version"] + 1)
    assert http_status(caught.value) == 412
    with pytest.raises(LoamzError) as caught:
        store.delete_securable("schema", schema, None)
    assert http_status(caught.value) == 409
