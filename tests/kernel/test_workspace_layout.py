from __future__ import annotations

import pytest

from substrate.agents.workspace.layout import (
    conversation_shared_key,
    conversation_workspace_prefix,
    knowledge_document_prefix,
    user_index_prefix,
    user_prefix,
)


def test_canonical_keys_are_tenant_first() -> None:
    assert user_prefix("tenant-a", "user-a") == "tenants/tenant-a/users/user-a"
    assert conversation_shared_key("tenant-a", "user-a", "thread-a", "uploads/report.pdf") == (
        "tenants/tenant-a/users/user-a/conversations/thread-a/branches/main/workspace"
        "/shared/uploads/report.pdf"
    )
    assert knowledge_document_prefix("tenant-a", "kb-a", "doc-a") == (
        "tenants/tenant-a/knowledge/kb-a/documents/doc-a"
    )
    assert user_index_prefix("tenant-a", "user-a") == (
        "tenants/tenant-a/users/user-a/index"
    )


def test_branch_dimension_is_uniform_not_special_cased() -> None:
    """'main' is just a branch id — no separate path shape from any other
    branch. This is the fix for the dead-end fork bug: the old layout gave
    main and non-main branches different prefixes, and only one of the two
    call sites that mattered (the code interpreter) knew about the branch
    parameter at all."""
    main = conversation_workspace_prefix("tenant-a", "user-a", "thread-a", "main")
    other = conversation_workspace_prefix("tenant-a", "user-a", "thread-a", "exp-1")
    assert main == "tenants/tenant-a/users/user-a/conversations/thread-a/branches/main/workspace"
    assert other == "tenants/tenant-a/users/user-a/conversations/thread-a/branches/exp-1/workspace"
    # Same shape, only the branch segment differs.
    assert main.replace("/main/", "/exp-1/") == other


@pytest.mark.parametrize("value", ["", "../other", "tenant/a"])
def test_identifiers_cannot_escape_storage_tree(value: str) -> None:
    with pytest.raises(ValueError):
        user_prefix(value, "user-a")


def test_relative_paths_cannot_traverse() -> None:
    with pytest.raises(ValueError):
        conversation_shared_key("tenant-a", "user-a", "thread-a", "../../secret")
