"""Identity Auth Service — business logic."""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from raavan.services.identity.models import IdentityUser
from raavan.shared.auth import jwt as jwt_utils
from raavan.shared.events.bus import EventBus
from raavan.shared.events import types as events

logger = logging.getLogger(__name__)


async def get_or_create_user(
    db: AsyncSession,
    identifier: str,
    email: str = "",
    role: str = "end_user",
    tenant_id: str = "default",
    provider: str = "local",
    provider_id: str | None = None,
) -> IdentityUser:
    """Find existing user by identifier or create a new one."""
    result = await db.execute(
        select(IdentityUser).where(IdentityUser.identifier == identifier)
    )
    user = result.scalar_one_or_none()
    if user:
        return user

    user = IdentityUser(
        identifier=identifier,
        email=email,
        role=role,
        tenant_id=tenant_id,
        provider=provider,
        provider_id=provider_id,
    )
    db.add(user)
    await db.flush()
    return user


async def get_user_by_id(db: AsyncSession, user_id: str) -> Optional[IdentityUser]:
    """Look up a user by UUID string."""
    import uuid

    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        return None
    result = await db.execute(select(IdentityUser).where(IdentityUser.id == uid))
    return result.scalar_one_or_none()


async def exchange_frontend_token(
    db: AsyncSession,
    frontend_token: str,
    jwt_secret: str,
    event_bus: Optional[EventBus] = None,
) -> dict:
    """Exchange a frontend-signed JWT for backend access + refresh tokens.

    The frontend signs a JWT with the shared secret containing user claims.
    We verify it, ensure the user exists, then issue platform tokens.
    """
    payload = jwt_utils.verify_token(frontend_token, secret=jwt_secret)
    if payload is None:
        raise ValueError("Invalid or expired frontend token")

    user = await get_or_create_user(
        db,
        identifier=payload.sub,
        email=payload.email,
        role=payload.role,
        tenant_id=payload.tenant_id,
    )

    access_token, expires_at = jwt_utils.create_access_token(
        user_id=str(user.id),
        email=user.email,
        role=user.role,
        tenant_id=user.tenant_id,
        secret=jwt_secret,
    )
    refresh_token, jti, refresh_exp = jwt_utils.create_refresh_token(
        user_id=str(user.id),
        secret=jwt_secret,
    )

    # Publish session event
    if event_bus:
        await event_bus.publish(
            events.session_started(
                user_id=str(user.id),
                session_id=jti,
                tenant_id=user.tenant_id,
            )
        )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "jti": jti,
        "refresh_exp": refresh_exp,
        "user": user,
    }
