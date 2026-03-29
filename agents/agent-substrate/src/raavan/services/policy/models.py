"""Policy Authorization Service — ORM models.

System of record for: roles, policies, grants.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from raavan.shared.database.base import ServiceBase


class PolicyRule(ServiceBase):
    """A policy rule defining what a role can do on a resource."""

    __tablename__ = "policy_rules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[str] = mapped_column(
        String, nullable=False, default="default", index=True
    )
    role: Mapped[str] = mapped_column(String, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String, nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String, nullable=False, default="*")
    effect: Mapped[str] = mapped_column(String, nullable=False, default="allow")
    conditions: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    priority: Mapped[int] = mapped_column(default=0)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return f"<PolicyRule(role={self.role!r}, action={self.action!r}, effect={self.effect!r})>"


class WorkspaceGrant(ServiceBase):
    """Workspace-level role binding for a user."""

    __tablename__ = "workspace_grants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String, nullable=False)
    granted_by: Mapped[str] = mapped_column(String, nullable=False, default="system")
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return f"<WorkspaceGrant(user={self.user_id!r}, role={self.role!r})>"
