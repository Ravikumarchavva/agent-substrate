"""Pinned chats: the few the user keeps at the top of their list, agents and groups together. On the account, so every
device shows the same."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from loamz.agents.bootstrap.container import Container
from loamz.agents.features.directory.pins.store import PinsStore
from loamz.agents.kernel.caller import Caller
from loamz.data.storage.errors import ConflictError

MAX_PINNED = 5


class PinsService:
    def __init__(self, store: PinsStore) -> None:
        self._store = store

    async def set_pinned(self, caller: Caller, kind: str, item_id: str, pinned: bool) -> None:
        """Pin or unpin. Pinning what is already pinned changes nothing; a sixth pin is refused."""
        if not pinned:
            await self._store.remove(caller.tenant_id, caller.user_id, kind, item_id)
            return
        if await self._store.get(caller.tenant_id, caller.user_id, kind, item_id) is not None:
            return
        if await self._store.count(caller.tenant_id, caller.user_id) >= MAX_PINNED:
            raise ConflictError(f"You can pin up to {MAX_PINNED} chats.")
        await self._store.add(caller.tenant_id, caller.user_id, kind, item_id, time.time())

    async def pinned_at(self, caller: Caller, kind: str) -> dict[str, datetime]:
        """`{item id: when pinned}` for the caller's pinned agents or groups."""
        return {
            item: datetime.fromtimestamp(at, UTC)
            for item, at in (
                await self._store.of_kind(caller.tenant_id, caller.user_id, kind)
            ).items()
        }

    async def forget(self, tenant_id: str, user_id: str, kind: str, item_id: str) -> None:
        await self._store.remove(tenant_id, user_id, kind, item_id)

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        return await self._store.erase(tenant_id, user_id)


def pins(container: Container) -> PinsService:
    return PinsService(PinsStore(container.database))
