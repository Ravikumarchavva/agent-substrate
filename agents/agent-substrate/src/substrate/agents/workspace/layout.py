"""Canonical, tenant-scoped object-storage keys.

Object keys are an authorization boundary. Keep their construction here so
routes, sandboxes and ingestion cannot silently grow incompatible layouts —
nothing outside this module should build a workspace object-storage key.

Moved down from ``capabilities/storage/layout.py`` (pure stdlib; it was L2
only by filing accident) as part of making branching real. The one
substantive change from the old module: the branch dimension is now
**uniform** — every workspace key includes ``/branches/{branch_id}/``,
including for ``"main"``. The old code special-cased ``branch_id == "main"``
to omit the segment, which is the root cause of the fork bug this package
fixes: ``conversation_shared_key`` (what the code interpreter and every
upload route actually write to) had no branch parameter at all, so it
always resolved to main's path regardless of which branch a fork's files
were copied into. "Main is just a branch id" — no special case.

All arguments are identifiers, never client-supplied paths.
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


def conversation_prefix(tenant_id: str, user_id: str, conversation_id: str) -> str:
    return (
        f"{user_prefix(tenant_id, user_id)}/conversations/"
        f"{_id(conversation_id, 'conversation id')}"
    )


def conversation_workspace_prefix(
    tenant_id: str,
    user_id: str,
    conversation_id: str,
    branch_id: str = "main",
) -> str:
    """The workspace prefix for one branch of one conversation.

    Nested under the owning user (not a tenant-level sibling of `users/`)
    so a single-prefix delete of `user_prefix(...)` — GDPR erasure, account
    deletion — removes every conversation the user ever had, every branch,
    along with it, without needing a separate per-conversation sweep.
    """
    base = conversation_prefix(tenant_id, user_id, conversation_id)
    return f"{base}/branches/{_id(branch_id, 'branch id')}/workspace"


def conversation_shared_key(
    tenant_id: str,
    user_id: str,
    conversation_id: str,
    path: str,
    branch_id: str = "main",
) -> str:
    return (
        f"{conversation_workspace_prefix(tenant_id, user_id, conversation_id, branch_id)}"
        f"/shared/{safe_relative_path(path)}"
    )


def conversation_version_key(
    tenant_id: str,
    user_id: str,
    conversation_id: str,
    path: str,
    branch_id: str = "main",
) -> str:
    return (
        f"{conversation_workspace_prefix(tenant_id, user_id, conversation_id, branch_id)}"
        f"/versions/{safe_relative_path(path)}"
    )


def user_upload_key(tenant_id: str, user_id: str, filename: str) -> str:
    """A direct (thread-less) upload — the one namespace the old layout left
    unbuilt; readers and writers previously agreed only by convention."""
    return f"{user_prefix(tenant_id, user_id)}/uploads/{safe_relative_path(filename)}"


def agent_private_prefix(
    tenant_id: str,
    user_id: str,
    conversation_id: str,
    agent_id: str,
    *,
    branch_id: str = "main",
    parent_agent_id: str | None = None,
) -> str:
    """An agent's (or subagent's) private scratch dir, invisible to other
    agents sharing the same branch workspace."""
    workspace = conversation_workspace_prefix(tenant_id, user_id, conversation_id, branch_id)
    if parent_agent_id:
        return (
            f"{workspace}/agents/{_id(parent_agent_id, 'parent agent id')}"
            f"/subagents/{_id(agent_id, 'agent id')}/private"
        )
    return f"{workspace}/agents/{_id(agent_id, 'agent id')}/private"


def blob_key(tenant_id: str, user_id: str, content_hash: str) -> str:
    """Content-addressed blob storage, scoped per user (not per tenant): a
    tenant-level CAS would break the GDPR single-prefix-delete rule
    ``conversation_workspace_prefix`` relies on, and per-user scoping also
    keeps garbage collection incremental and avoids cross-tenant dedup
    (a privacy decision, not just a storage one — see the workspace plan)."""
    h = _id(content_hash, "content hash")
    return f"{user_prefix(tenant_id, user_id)}/blobs/{h[:2]}/{h}"


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
    return f"{conversation_prefix(tenant_id, user_id, conversation_id)}/artifacts"


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


__all__ = [
    "tenant_prefix",
    "user_prefix",
    "conversation_prefix",
    "conversation_workspace_prefix",
    "conversation_shared_key",
    "conversation_version_key",
    "user_upload_key",
    "agent_private_prefix",
    "blob_key",
    "user_artifacts_prefix",
    "conversation_artifacts_prefix",
    "user_index_prefix",
    "knowledge_document_prefix",
    "safe_relative_path",
]
