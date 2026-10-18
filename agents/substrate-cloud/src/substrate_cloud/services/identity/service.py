"""Identity service: profile mirror logic."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from substrate_cloud.services.identity.models import IdentityUser
from substrate_cloud.shared.auth.claims import AuthClaims


async def upsert_profile(
    db: AsyncSession, claims: AuthClaims, display_name: str = ""
) -> IdentityUser:
    """Create or refresh the caller's profile from their verified token."""
    row = (
        await db.execute(
            select(IdentityUser).where(
                IdentityUser.tenant_id == claims.tenant_id,
                IdentityUser.subject == claims.sub,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = IdentityUser(
            subject=claims.sub,
            tenant_id=claims.tenant_id,
            email=claims.email,
            display_name=display_name,
            role=claims.role,
        )
        db.add(row)
    else:
        row.email, row.role = claims.email, claims.role
        if display_name:
            row.display_name = display_name
    await db.flush()
    return row


async def find_profile(
    db: AsyncSession, tenant_id: str, subject: str
) -> IdentityUser | None:
    return (
        await db.execute(
            select(IdentityUser).where(
                IdentityUser.tenant_id == tenant_id, IdentityUser.subject == subject
            )
        )
    ).scalar_one_or_none()
