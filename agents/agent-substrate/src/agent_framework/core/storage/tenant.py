"""Tenant-aware path builder for file storage.

Constructs deterministic, collision-free object keys following the layout::

    org/{org_id}/user/{user_id}/thread/{thread_id}/{scope}/{filename}

Each component is optional — ``TenantContext`` adapts to the information
available (e.g. anonymous users omit ``org_id``).

Scopes:
  - ``uploads``   – user-uploaded files
  - ``generated`` – agent/tool-generated artefacts
  - ``exports``   – CI output snapshots, reports
  - ``temp``      – ephemeral scratch files (auto-purged)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class FileScope(str, Enum):
    """Logical namespace within a thread's file space."""

    UPLOADS = "uploads"
    GENERATED = "generated"
    EXPORTS = "exports"
    TEMP = "temp"


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Immutable tenant identity for path construction.

    Attributes:
        org_id: Organisation identifier (optional for single-tenant).
        user_id: Authenticated user identifier.
        thread_id: Conversation / session identifier.
    """

    org_id: Optional[str] = None
    user_id: Optional[str] = None
    thread_id: Optional[str] = None

    # ── path construction ────────────────────────────────────────────────

    def prefix(self) -> str:
        """Build the base prefix for this tenant.

        Examples:
            TenantContext("acme", "u-123", "t-456").prefix()
            → "org/acme/user/u-123/thread/t-456/"

            TenantContext(user_id="u-123", thread_id="t-456").prefix()
            → "user/u-123/thread/t-456/"

            TenantContext(thread_id="t-456").prefix()
            → "thread/t-456/"

            TenantContext().prefix()
            → "global/"
        """
        parts: list[str] = []
        if self.org_id:
            parts.extend(["org", self.org_id])
        if self.user_id:
            parts.extend(["user", self.user_id])
        if self.thread_id:
            parts.extend(["thread", self.thread_id])
        if not parts:
            parts.append("global")
        return "/".join(parts) + "/"

    def key(
        self,
        filename: str,
        scope: FileScope = FileScope.UPLOADS,
        *,
        unique: bool = True,
    ) -> str:
        """Build a full object key for a file.

        Parameters:
            filename: Original user-facing filename (e.g. ``"report.csv"``).
            scope: Logical sub-namespace.
            unique: If ``True`` (default), prepend a UUID4 to prevent
                    collisions when users upload files with the same name.

        Returns:
            A ``/``-delimited object key like::

                org/acme/user/u-1/thread/t-2/uploads/a1b2c3d4/report.csv
        """
        safe_name = _sanitise_filename(filename)
        if unique:
            return f"{self.prefix()}{scope.value}/{uuid.uuid4().hex[:16]}/{safe_name}"
        return f"{self.prefix()}{scope.value}/{safe_name}"

    def scope_prefix(self, scope: FileScope) -> str:
        """Return the prefix for an entire scope.

        Useful for listing or purging all uploads within a thread.
        """
        return f"{self.prefix()}{scope.value}/"


def _sanitise_filename(name: str) -> str:
    """Strip path separators and dangerous characters from a filename.

    Keeps alphanumerics, hyphens, underscores, and dots.
    """
    # Take only the basename if path separators are present
    basename = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    # Replace spaces with underscores
    basename = basename.replace(" ", "_")
    # Keep only safe characters
    safe = "".join(c for c in basename if c.isalnum() or c in "-_.")
    return safe or "unnamed"
