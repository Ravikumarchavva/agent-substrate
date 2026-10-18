import re

import pytest

from loamz.identity import tenancy
from loamz.identity.tenancy import (
    TenancyError,
    catalog_name,
    files_volume,
    org_group,
    split_key,
    split_prefix,
    tenant_alias,
    tenant_key,
)


def test_aliases_are_deterministic_and_safe_names():
    assert tenant_alias("acme-corp") == "acme_corp"
    uuid = "2f8548d4-bdce-4a61-80a5-db6d5e12387e"
    assert tenant_alias(uuid) == "t2f8548d4_bdce_4a61_80a5_db6d5e12387e"
    assert len(tenant_alias("x" * 128)) == 56
    for tenant in ("acme-corp", uuid, "A.B_c", "9lives"):
        assert re.match(tenancy.ALIAS_PATTERN, tenant_alias(tenant))


def test_names_derive_from_the_tenant_id():
    assert catalog_name("acme") == "tenant_acme"
    assert files_volume("acme") == "tenant_acme.workspace.files"
    assert org_group("acme") == "org:acme"


@pytest.mark.parametrize("bad", ["", "-x", "a/b", "a b", "x" * 129])
def test_bad_tenant_ids_are_refused(bad):
    with pytest.raises(TenancyError):
        tenant_alias(bad)


def test_keys_round_trip_and_never_climb_out():
    key = tenant_key("acme", "users/u1/notes.txt")
    assert key == "tenants/acme/users/u1/notes.txt"
    assert split_key(key) == ("acme", "users/u1/notes.txt")
    assert split_prefix("tenants/acme") == ("acme", "")
    for bad in ("users/u1/a", "tenants//a", "tenants/acme/../other/a", "tenants/acme/a\\b"):
        with pytest.raises(TenancyError):
            split_key(bad)
