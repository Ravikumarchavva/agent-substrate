"""Per-tenant entitlements: the limits the platform sets and this cell enforces."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from substrate_cloud.monolith.models import TenantEntitlement


@dataclass(frozen=True)
class Entitlements:
    tenant_id: str
    daily_message_limit: int | None = None  # None: unlimited
    features: dict[str, Any] = field(default_factory=dict)


async def get_entitlements(db: AsyncSession, tenant_id: str) -> Entitlements:
    """The tenant's entitlements; unlimited defaults when nothing was configured."""
    row = (
        await db.execute(
            select(TenantEntitlement).where(TenantEntitlement.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if row is None:
        return Entitlements(tenant_id)
    return Entitlements(tenant_id, row.daily_message_limit, dict(row.features or {}))


async def set_entitlements(
    db: AsyncSession,
    tenant_id: str,
    *,
    daily_message_limit: int | None,
    features: dict[str, Any],
    updated_by: str,
) -> Entitlements:
    """Replace the tenant's entitlements (idempotent)."""
    values = {
        "tenant_id": tenant_id,
        "daily_message_limit": daily_message_limit,
        "features": features,
        "updated_by": updated_by,
    }
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        statement = pg_insert(TenantEntitlement).values(**values)
        await db.execute(
            statement.on_conflict_do_update(
                index_elements=[TenantEntitlement.tenant_id],
                set_={k: v for k, v in values.items() if k != "tenant_id"},
            )
        )
    else:
        row = await db.get(TenantEntitlement, tenant_id)
        if row is None:
            db.add(TenantEntitlement(**values))
        else:
            for key, value in values.items():
                setattr(row, key, value)
    await db.commit()
    return Entitlements(tenant_id, daily_message_limit, features)
