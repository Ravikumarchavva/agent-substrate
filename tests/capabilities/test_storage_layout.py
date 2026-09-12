from __future__ import annotations

import pytest

from substrate.capabilities.storage.layout import (
    conversation_shared_key,
    knowledge_document_prefix,
    user_prefix,
)


def test_canonical_keys_are_tenant_first() -> None:
    assert user_prefix("tenant-a", "user-a") == "tenants/tenant-a/users/user-a"
    assert conversation_shared_key("tenant-a", "thread-a", "uploads/report.pdf") == (
        "tenants/tenant-a/conversations/thread-a/workspace/shared/uploads/report.pdf"
    )
    assert knowledge_document_prefix("tenant-a", "kb-a", "doc-a") == (
        "tenants/tenant-a/knowledge/kb-a/documents/doc-a"
    )


@pytest.mark.parametrize("value", ["", "../other", "tenant/a"])
def test_identifiers_cannot_escape_storage_tree(value: str) -> None:
    with pytest.raises(ValueError):
        user_prefix(value, "user-a")


def test_relative_paths_cannot_traverse() -> None:
    with pytest.raises(ValueError):
        conversation_shared_key("tenant-a", "thread-a", "../../secret")
