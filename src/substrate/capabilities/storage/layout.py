"""Canonical, tenant-scoped object-storage keys.

Object keys are an authorization boundary.  Keep their construction here so
routes, sandboxes and ingestion cannot silently grow incompatible layouts.
All arguments are identifiers, never client supplied paths.
"""

from __future__ import annotations

from pathlib import PurePosixPath


def _id(value: str, name: str) -> str:
    if not value or "/" in value or "\\" in value or value in {".", ".."}:
        raise ValueError(f"invalid {name}")
    return value


def safe_relative_path(path: str) -> str:
    value = path.lstrip("/")
    if not value or ".." in PurePosixPath(value).parts:
        raise ValueError("invalid relative path")
    return value


def tenant_prefix(tenant_id: str) -> str:
    return f"tenants/{_id(tenant_id, 'tenant id')}"


def user_prefix(tenant_id: str, user_id: str) -> str:
    return f"{tenant_prefix(tenant_id)}/users/{_id(user_id, 'user id')}"


def conversation_workspace_prefix(tenant_id: str, conversation_id: str) -> str:
    return (
        f"{tenant_prefix(tenant_id)}/conversations/"
        f"{_id(conversation_id, 'conversation id')}/workspace"
    )


def conversation_shared_key(tenant_id: str, conversation_id: str, path: str) -> str:
    return f"{conversation_workspace_prefix(tenant_id, conversation_id)}/shared/{safe_relative_path(path)}"


def conversation_version_key(tenant_id: str, conversation_id: str, path: str) -> str:
    return f"{conversation_workspace_prefix(tenant_id, conversation_id)}/versions/{safe_relative_path(path)}"


def knowledge_document_prefix(
    tenant_id: str, knowledge_base_id: str, document_id: str
) -> str:
    return (
        f"{tenant_prefix(tenant_id)}/knowledge/{_id(knowledge_base_id, 'knowledge base id')}"
        f"/documents/{_id(document_id, 'document id')}"
    )
