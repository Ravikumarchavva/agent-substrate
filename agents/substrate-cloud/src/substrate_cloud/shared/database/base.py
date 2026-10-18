"""Shared database base model for all service-owned tables."""

from __future__ import annotations

from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase

# JSON that is JSONB on PostgreSQL and plain JSON elsewhere (the services run on SQLite in tests).
Json = JSON().with_variant(JSONB(), "postgresql")


class ServiceBase(DeclarativeBase):
    """Declarative base for service-owned ORM models.

    Each service uses this as its base class. During local dev, all services
    share the same PostgreSQL database but own separate tables.
    In production, each service may use its own database instance.
    """

    pass
