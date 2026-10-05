"""Pinned chats: the few the user keeps at the top of their list, agents and groups together. On the account, so every device shows the same."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Union

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from substrate_cloud.monolith.models import Agent, Group

MAX_PINNED = 5


class TooManyPinned(Exception):
    pass


async def pinned_count(db: AsyncSession, tenant_id: str, user_id: str) -> int:
    total = 0
    for model in (Agent, Group):
        total += (
            await db.execute(
                select(func.count()).where(model.tenant_id == tenant_id, model.user_identifier == user_id, model.pinned_at.is_not(None))
            )
        ).scalar_one()
    return total


async def set_pinned(db: AsyncSession, row: Union[Agent, Group], pinned: bool) -> None:
    """Pin or unpin. Pinning what is already pinned changes nothing; a sixth pin is refused."""
    if pinned and row.pinned_at is None:
        if await pinned_count(db, row.tenant_id, row.user_identifier) >= MAX_PINNED:
            raise TooManyPinned(f"You can pin up to {MAX_PINNED} chats.")
        row.pinned_at = datetime.now(timezone.utc)
    elif not pinned:
        row.pinned_at = None
    await db.flush()
