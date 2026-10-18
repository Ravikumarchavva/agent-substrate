"""Identity service: ORM models.

A profile mirror of the people who have used this cell. Keycloak is the system of record for accounts, credentials,
organizations and roles; a row here is created and refreshed from a verified token, never from a password.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from substrate_cloud.shared.database.base import ServiceBase


class IdentityUser(ServiceBase):
    __tablename__ = "identity_users"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "subject", name="uq_identity_users_tenant_subject"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    subject: Mapped[str] = mapped_column(
        String, nullable=False, index=True
    )  # the Keycloak user id
    tenant_id: Mapped[str] = mapped_column(
        String, nullable=False, index=True
    )  # the Keycloak organization id
    email: Mapped[str] = mapped_column(String, nullable=False, default="")
    display_name: Mapped[str] = mapped_column(String, nullable=False, default="")
    role: Mapped[str] = mapped_column(String, nullable=False, default="end_user")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<IdentityUser(id={self.id}, subject={self.subject!r}, tenant={self.tenant_id!r})>"
