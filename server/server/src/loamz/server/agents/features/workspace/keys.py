"""Object-key shapes of the workspace, read structurally.

Every key starts `tenants/<tenant>/` and was built by `stores.workspace.layout`; these helpers only take them apart.
"""

from __future__ import annotations

# The directory snapshots live in, a sibling of `shared/` (hidden from the Storage browser).
VERSIONS_DIR = "versions"
# Workspaces that belong to someone rather than a conversation: an agent's home and a group's drive.
PERSISTENT_PREFIXES = ("dot-", "group-")
ADMIN_ROLES = ("platform_admin", "tenant_admin")


def is_conversation_shared_key(key: str) -> bool:
    """A file under `tenants/<t>/users/<u>/conversations/<c>/branches/<b>/workspace/shared/...`."""
    parts = key.split("/")
    return (
        len(parts) >= 11
        and parts[0] == "tenants"
        and parts[2] == "users"
        and parts[4] == "conversations"
        and parts[6] == "branches"
        and parts[8] == "workspace"
        and parts[9] == "shared"
    )


def is_direct_upload_key(key: str) -> bool:
    """An upload with no conversation: `tenants/<t>/users/<u>/uploads/...`."""
    parts = key.split("/")
    return (
        len(parts) >= 5 and parts[0] == "tenants" and parts[2] == "users" and parts[4] == "uploads"
    )


def is_listable_key(key: str) -> bool:
    """Only conversation-shared files and direct uploads are the user's files (not versions, artifacts, indexes)."""
    return is_conversation_shared_key(key) or is_direct_upload_key(key)


def is_version_key(key: str) -> bool:
    parts = key.split("/")
    return (
        len(parts) >= 10
        and parts[0] == "tenants"
        and parts[2] == "users"
        and parts[4] == "conversations"
        and parts[6] == "branches"
        and parts[8] == "workspace"
        and parts[9] == VERSIONS_DIR
    )


def session_id_from_key(key: str) -> str | None:
    """The conversation (or agent/group workspace) id in `tenants/<t>/users/<u>/conversations/<id>/...`."""
    parts = key.split("/")
    if (
        len(parts) >= 6
        and parts[0] == "tenants"
        and parts[2] == "users"
        and parts[4] == "conversations"
    ):
        return parts[5]
    return None


def session_relative_path(object_key: str) -> str | None:
    """`.../workspace/shared/<rest>` -> `rest`, or `None` for a key that is not a conversation upload."""
    parts = object_key.split("/", 10)
    if is_conversation_shared_key(object_key) and len(parts) == 11:
        return parts[10]
    return None


def key_owner(key: str) -> str | None:
    """The user whose folder an object key is in (`tenants/<t>/users/<u>/...`), or `None`."""
    parts = key.split("/")
    if len(parts) >= 4 and parts[0] == "tenants" and parts[2] == "users" and parts[3]:
        return parts[3]
    return None


def is_inline_type(content_type: str) -> bool:
    """Types browsers can render inline; everything else forces a download."""
    return content_type.startswith(("image/", "text/")) or content_type == "application/pdf"


__all__ = [
    "ADMIN_ROLES",
    "PERSISTENT_PREFIXES",
    "VERSIONS_DIR",
    "is_conversation_shared_key",
    "is_direct_upload_key",
    "is_inline_type",
    "is_listable_key",
    "is_version_key",
    "key_owner",
    "session_id_from_key",
    "session_relative_path",
]
