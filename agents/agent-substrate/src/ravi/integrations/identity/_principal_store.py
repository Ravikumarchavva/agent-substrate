"""PrincipalStore — register, lookup, and list PrincipalId records.

The ``PrincipalStore`` Protocol is the persistence boundary for kernel
:class:`PrincipalId` objects.  The ``PostgresPrincipalStore`` implements it
using SQLAlchemy 2 async with a ``principals`` table.

Schema
------
The ``principals`` table mirrors :class:`PrincipalId` exactly; the ``uid``
column is the primary key, ``fqn`` has a unique index for fast lookups, and
``fingerprint`` is stored for audit queries.

Importing this module in the kernel layer is forbidden — it belongs in
``integrations/identity/`` because it depends on SQLAlchemy.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol, Sequence, runtime_checkable

from sqlalchemy import DateTime, String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from ravi.kernel.runtime._identity import (
    PrincipalId,
    PrincipalKind,
)
from ravi.shared.database.base import ServiceBase

__all__ = [
    "PostgresPrincipalStore",
    "PrincipalNotFound",
    "PrincipalRecord",
    "PrincipalStore",
]

UTC = timezone.utc


# ---------------------------------------------------------------------------
# ORM model
# ---------------------------------------------------------------------------


class PrincipalRecord(ServiceBase):
    """SQLAlchemy ORM model for the ``principals`` table."""

    __tablename__ = "principals"

    uid: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    fqn: Mapped[str] = mapped_column(String(768), unique=True, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    def to_principal_id(self) -> PrincipalId:
        """Reconstruct the kernel :class:`PrincipalId` from this record."""
        return PrincipalId(
            kind=PrincipalKind[self.kind],
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            name=self.name,
            uid=self.uid,
        )

    @classmethod
    def from_principal_id(cls, principal: PrincipalId) -> PrincipalRecord:
        return cls(
            uid=principal.uid,
            kind=principal.kind.name,
            tenant_id=principal.tenant_id,
            workspace_id=principal.workspace_id,
            name=principal.name,
            fqn=principal.fqn,
            fingerprint=principal.fingerprint,
        )


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class PrincipalNotFound(KeyError):
    """Raised by :meth:`PrincipalStore.get` when the uid is absent."""

    def __init__(self, uid: str) -> None:
        self.uid = uid
        super().__init__(uid)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class PrincipalStore(Protocol):
    """Persistence contract for :class:`PrincipalId` records.

    All mutations are upsert-safe: registering the same principal twice
    (identified by ``uid``) is idempotent — the second call updates the
    mutable fields (kind, name) but preserves ``created_at``.
    """

    async def register(self, principal: PrincipalId) -> PrincipalId:
        """Upsert ``principal``; return the persisted copy."""
        ...

    async def get(self, uid: str) -> PrincipalId:
        """Return the principal for ``uid``; raise :class:`PrincipalNotFound`."""
        ...

    async def get_or_none(self, uid: str) -> PrincipalId | None:
        """Return the principal for ``uid`` or ``None``."""
        ...

    async def get_by_fqn(self, fqn: str) -> PrincipalId | None:
        """Return the principal for the fully-qualified name or ``None``."""
        ...

    async def list_for_tenant(
        self,
        tenant_id: str,
        *,
        workspace_id: str | None = None,
        limit: int = 100,
    ) -> Sequence[PrincipalId]:
        """List principals in a tenant, optionally filtered by workspace."""
        ...

    async def delete(self, uid: str) -> bool:
        """Remove the principal. Return ``True`` when something was deleted."""
        ...


# ---------------------------------------------------------------------------
# Postgres implementation
# ---------------------------------------------------------------------------


class PostgresPrincipalStore:
    """SQLAlchemy 2 async implementation of :class:`PrincipalStore`.

    Each method opens its own session or accepts one via ``session`` kwarg
    — a new session is created from ``session_factory`` when none is passed.
    For request-scoped usage (FastAPI DI), pass the session directly.
    """

    def __init__(self, session_factory: object) -> None:
        """
        Parameters
        ----------
        session_factory:
            An async_sessionmaker that produces :class:`AsyncSession` objects.
        """
        self._factory = session_factory

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _session(self) -> AsyncSession:
        return self._factory()  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # PrincipalStore protocol
    # ------------------------------------------------------------------

    async def register(self, principal: PrincipalId) -> PrincipalId:
        record = PrincipalRecord.from_principal_id(principal)
        async with self._session() as session:
            existing = await session.get(PrincipalRecord, principal.uid)
            if existing is None:
                session.add(record)
            else:
                existing.kind = principal.kind.name
                existing.name = principal.name
                existing.fqn = principal.fqn
                existing.fingerprint = principal.fingerprint
            await session.commit()
        return principal

    async def get(self, uid: str) -> PrincipalId:
        result = await self.get_or_none(uid)
        if result is None:
            raise PrincipalNotFound(uid)
        return result

    async def get_or_none(self, uid: str) -> PrincipalId | None:
        async with self._session() as session:
            record = await session.get(PrincipalRecord, uid)
            if record is None:
                return None
            return record.to_principal_id()

    async def get_by_fqn(self, fqn: str) -> PrincipalId | None:
        async with self._session() as session:
            stmt = select(PrincipalRecord).where(PrincipalRecord.fqn == fqn)
            result = await session.execute(stmt)
            record = result.scalar_one_or_none()
            if record is None:
                return None
            return record.to_principal_id()

    async def list_for_tenant(
        self,
        tenant_id: str,
        *,
        workspace_id: str | None = None,
        limit: int = 100,
    ) -> Sequence[PrincipalId]:
        async with self._session() as session:
            stmt = (
                select(PrincipalRecord)
                .where(PrincipalRecord.tenant_id == tenant_id)
                .limit(limit)
            )
            if workspace_id is not None:
                stmt = stmt.where(PrincipalRecord.workspace_id == workspace_id)
            result = await session.execute(stmt)
            return [row.to_principal_id() for row in result.scalars()]

    async def delete(self, uid: str) -> bool:
        async with self._session() as session:
            record = await session.get(PrincipalRecord, uid)
            if record is None:
                return False
            await session.delete(record)
            await session.commit()
            return True
