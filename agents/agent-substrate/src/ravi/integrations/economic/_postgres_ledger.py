"""PostgreSQL-backed BudgetLedger and EconomicSignalSource.

Implements the kernel economic-plane contracts using SQLAlchemy 2.0 async
ORM with asyncpg.  All financial operations use row-level locking (SELECT
FOR UPDATE) so concurrent reserve/commit/release calls remain safe.

Tables (created by ``init_db()``)
----------------------------------
``economic_balances``     — per-principal deposited / committed / reserved totals
``economic_reservations`` — live reservation records (deleted on commit/release)
``economic_signals``      — audit trail of economic-plane warning signals
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import DateTime, Float, Index, String, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ravi.kernel.economic import (
    BudgetExhausted,
    EconomicSignal,
    EconomicSignalKind,
    ReservationLost,
    ReservationToken,
)
from ravi.kernel.runtime._identity import PrincipalId

__all__ = ["PostgresBudgetLedger"]

_UTC = timezone.utc
_SOURCE_ID = "postgres_budget_ledger"
_SIG_LIMIT = 64


# ---------------------------------------------------------------------------
# ORM models — separate DeclarativeBase to avoid collisions
# ---------------------------------------------------------------------------


class EconomicBase(DeclarativeBase):
    """Separate declarative base for the economic-plane subsystem."""

    pass


class EconomicBalance(EconomicBase):
    """Running totals for a single principal."""

    __tablename__ = "economic_balances"

    principal_fqn: Mapped[str] = mapped_column(String(512), primary_key=True)
    deposited: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    committed: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    reserved: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)


class EconomicReservation(EconomicBase):
    """A live reservation record — deleted on commit or release."""

    __tablename__ = "economic_reservations"
    __table_args__ = (
        Index("ix_econ_res_principal", "principal_fqn"),
        Index("ix_econ_res_expires", "expires_at"),
    )

    token_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    principal_fqn: Mapped[str] = mapped_column(String(512), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EconomicSignalRow(EconomicBase):
    """Persistent record of an economic-plane warning signal."""

    __tablename__ = "economic_signals"
    __table_args__ = (Index("ix_econ_sig_principal", "principal_fqn"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex)
    principal_fqn: Mapped[str] = mapped_column(String(512), nullable=False)
    signal_type: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    detail: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ---------------------------------------------------------------------------
# PostgresBudgetLedger
# ---------------------------------------------------------------------------


class PostgresBudgetLedger:
    """PostgreSQL-backed :class:`BudgetLedger` and :class:`EconomicSignalSource`.

    Parameters
    ----------
    database_url:
        SQLAlchemy-compatible async connection URL, e.g.
        ``postgresql+asyncpg://user:pass@localhost/agentdb``.
    pool_size:
        Number of connections in the connection pool (default 5).
    """

    def __init__(
        self,
        database_url: str,
        *,
        pool_size: int = 5,
    ) -> None:
        self._database_url = database_url
        self._pool_size = pool_size
        self._engine: Optional[AsyncEngine] = None
        self._session_factory: Optional[async_sessionmaker[AsyncSession]] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def init_db(self) -> None:
        """Create the engine, session factory, and ensure tables exist."""
        if self._engine is not None:
            return
        self._engine = create_async_engine(
            self._database_url,
            pool_size=self._pool_size,
            pool_pre_ping=True,
        )
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with self._engine.begin() as conn:
            await conn.run_sync(EconomicBase.metadata.create_all)

    def _get_session(self) -> async_sessionmaker[AsyncSession]:
        if self._session_factory is None:
            raise RuntimeError(
                "PostgresBudgetLedger not initialised. Call await init_db() first."
            )
        return self._session_factory

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _expire_reservations(self, db: AsyncSession, now: datetime) -> None:
        """Delete expired reservation rows and adjust reserved balances."""
        stmt = select(EconomicReservation).where(
            EconomicReservation.expires_at <= now
        )
        result = await db.execute(stmt)
        expired = result.scalars().all()
        for res in expired:
            balance = await db.get(
                EconomicBalance, res.principal_fqn, with_for_update=True
            )
            if balance is not None:
                balance.reserved = max(0.0, balance.reserved - res.amount)
            await db.delete(res)

    async def _get_or_create_balance(
        self, db: AsyncSession, fqn: str
    ) -> EconomicBalance:
        balance = await db.get(EconomicBalance, fqn, with_for_update=True)
        if balance is None:
            balance = EconomicBalance(
                principal_fqn=fqn,
                deposited=0.0,
                committed=0.0,
                reserved=0.0,
            )
            db.add(balance)
        return balance

    async def _record_signal(
        self,
        db: AsyncSession,
        fqn: str,
        kind: EconomicSignalKind,
        value: float,
        detail: str,
        now: datetime,
    ) -> None:
        row = EconomicSignalRow(
            id=uuid.uuid4().hex,
            principal_fqn=fqn,
            signal_type=kind.value,
            value=max(0.0, min(1.0, value)),
            detail=detail,
            issued_at=now,
        )
        db.add(row)

    # ------------------------------------------------------------------
    # BudgetLedger protocol
    # ------------------------------------------------------------------

    async def reserve(
        self,
        principal: PrincipalId,
        amount: float,
        *,
        ttl_seconds: float = 60.0,
    ) -> ReservationToken:
        if amount < 0:
            raise ValueError("amount must be >= 0")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")

        fqn = principal.fqn
        now = datetime.now(_UTC)
        expires_at = now + timedelta(seconds=ttl_seconds)
        token_id = uuid.uuid4().hex

        factory = self._get_session()
        async with factory() as db:
            async with db.begin():
                await self._expire_reservations(db, now)
                balance = await self._get_or_create_balance(db, fqn)

                available = max(0.0, balance.deposited - balance.committed - balance.reserved)
                if available < amount:
                    await self._record_signal(
                        db,
                        fqn,
                        EconomicSignalKind.BUDGET_EXHAUSTED,
                        1.0,
                        f"requested={amount} available={available}",
                        now,
                    )
                    raise BudgetExhausted(fqn, amount, available)

                balance.reserved += amount
                reservation = EconomicReservation(
                    token_id=token_id,
                    principal_fqn=fqn,
                    amount=amount,
                    granted_at=now,
                    expires_at=expires_at,
                )
                db.add(reservation)

        return ReservationToken(
            token_id=token_id,
            principal_fqn=fqn,
            amount=amount,
            granted_at=now.isoformat(),
            expires_at=expires_at.isoformat(),
        )

    async def commit(self, token: ReservationToken) -> None:
        factory = self._get_session()
        async with factory() as db:
            async with db.begin():
                reservation = await db.get(
                    EconomicReservation, token.token_id, with_for_update=True
                )
                if reservation is None:
                    raise ReservationLost(token.token_id)

                balance = await self._get_or_create_balance(db, token.principal_fqn)
                balance.reserved = max(0.0, balance.reserved - reservation.amount)
                balance.committed += reservation.amount
                await db.delete(reservation)

    async def release(self, token: ReservationToken) -> None:
        factory = self._get_session()
        async with factory() as db:
            async with db.begin():
                reservation = await db.get(
                    EconomicReservation, token.token_id, with_for_update=True
                )
                if reservation is None:
                    return  # no-op: already gone or expired

                balance = await db.get(
                    EconomicBalance, token.principal_fqn, with_for_update=True
                )
                if balance is not None:
                    balance.reserved = max(0.0, balance.reserved - reservation.amount)
                await db.delete(reservation)

    async def available_for(self, principal: PrincipalId) -> float:
        factory = self._get_session()
        async with factory() as db:
            balance = await db.get(EconomicBalance, principal.fqn)
            if balance is None:
                return 0.0
            return max(0.0, balance.deposited - balance.committed - balance.reserved)

    async def deposit(self, principal: PrincipalId, amount: float) -> None:
        if amount < 0:
            raise ValueError("amount must be >= 0")
        fqn = principal.fqn
        factory = self._get_session()
        async with factory() as db:
            async with db.begin():
                balance = await self._get_or_create_balance(db, fqn)
                balance.deposited += amount

    # ------------------------------------------------------------------
    # EconomicSignalSource protocol
    # ------------------------------------------------------------------

    async def signals_for(
        self, principal: PrincipalId
    ) -> tuple[EconomicSignal, ...]:
        factory = self._get_session()
        async with factory() as db:
            stmt = (
                select(EconomicSignalRow)
                .where(EconomicSignalRow.principal_fqn == principal.fqn)
                .order_by(EconomicSignalRow.issued_at.desc())
                .limit(_SIG_LIMIT)
            )
            result = await db.execute(stmt)
            rows = result.scalars().all()

        signals: list[EconomicSignal] = []
        for row in rows:
            try:
                kind = EconomicSignalKind(row.signal_type)
            except ValueError:
                continue
            signals.append(
                EconomicSignal(
                    signal_type=kind,
                    principal_fqn=row.principal_fqn,
                    value=row.value,
                    source_id=_SOURCE_ID,
                    issued_at=row.issued_at.isoformat(),
                    detail=row.detail,
                )
            )
        return tuple(signals)
