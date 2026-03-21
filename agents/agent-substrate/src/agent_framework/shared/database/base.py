"""Shared database base model for all service-owned tables."""
from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class ServiceBase(DeclarativeBase):
    """Declarative base for service-owned ORM models.

    Each service uses this as its base class. During local dev, all services
    share the same PostgreSQL database but own separate tables.
    In production, each service may use its own database instance.
    """
    pass
