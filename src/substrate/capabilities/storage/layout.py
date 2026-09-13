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


def conversation_workspace_prefix(
    tenant_id: str, user_id: str, conversation_id: str
) -> str:
    """Nested under the owning user (not a tenant-level sibling of `users/`)
    so a single-prefix delete of `user_prefix(...)` — GDPR erasure, account
    deletion — removes every conversation the user ever had along with it,
    without needing a separate per-conversation sweep."""
    return (
        f"{user_prefix(tenant_id, user_id)}/conversations/"
        f"{_id(conversation_id, 'conversation id')}/workspace"
    )


def conversation_shared_key(
    tenant_id: str, user_id: str, conversation_id: str, path: str
) -> str:
    return f"{conversation_workspace_prefix(tenant_id, user_id, conversation_id)}/shared/{safe_relative_path(path)}"


def conversation_version_key(
    tenant_id: str, user_id: str, conversation_id: str, path: str
) -> str:
    return f"{conversation_workspace_prefix(tenant_id, user_id, conversation_id)}/versions/{safe_relative_path(path)}"


def user_artifacts_prefix(tenant_id: str, user_id: str) -> str:
    """Global (cross-conversation) artifact bundle for one user — an OKF
    bundle (``https://github.com/GoogleCloudPlatform/open-knowledge-format``):
    markdown concepts with YAML frontmatter, plus the reserved ``index.md``
    and ``log.md``. Holds whatever survives a single conversation
    (``type: Memory`` facts today, promoted files and other types later)."""
    return f"{user_prefix(tenant_id, user_id)}/artifacts"


def conversation_artifacts_prefix(
    tenant_id: str, user_id: str, conversation_id: str
) -> str:
    """Session-scoped artifact bundle, a *sibling* of the conversation's
    ``workspace`` rather than a child of it.

    Deliberately outside ``workspace/shared``: that prefix is bind-mounted
    into the code-interpreter sandbox (see ``code_interpreter/tool.py``) and
    enumerated as the user's files, so nesting curated artifacts under it
    would both expose them to arbitrary sandboxed code and make every note
    show up in the file explorer. Sandbox output stays ordinary workspace
    files; something reaches this prefix only when explicitly promoted."""
    return (
        f"{user_prefix(tenant_id, user_id)}/conversations/"
        f"{_id(conversation_id, 'conversation id')}/artifacts"
    )


def user_index_prefix(tenant_id: str, user_id: str) -> str:
    """Per-user session-document index bundle (vectors, PageIndex tree
    nodes, entity/relationship graph) — see docs/claude_docs for the Lance
    table shapes stored under this prefix. Deliberately per-user, not
    per-conversation: lets a search span the user's own recent sessions
    (filtered by a `session_id` column on each table) instead of being
    blind past one conversation's boundary."""
    return f"{user_prefix(tenant_id, user_id)}/index"


def knowledge_document_prefix(
    tenant_id: str, knowledge_base_id: str, document_id: str
) -> str:
    return (
        f"{tenant_prefix(tenant_id)}/knowledge/{_id(knowledge_base_id, 'knowledge base id')}"
        f"/documents/{_id(document_id, 'document id')}"
    )
