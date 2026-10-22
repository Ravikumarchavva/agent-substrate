from __future__ import annotations

from loamz.engine.agents.kernel.caller import Caller
from loamz.server.agents.bootstrap.container import Container
from loamz.server.agents.features.people.preferences.model import Preferences
from loamz.server.agents.features.people.preferences.store import PreferencesStore


class PreferencesService:
    """The public API of this feature: other features (chat reads `instructions_for`) use this, never the store."""

    def __init__(self, store: PreferencesStore) -> None:
        self._store = store

    async def get(self, caller: Caller) -> Preferences:
        return await self._store.get(caller.tenant_id, caller.user_id) or Preferences()

    async def replace(self, caller: Caller, preferences: Preferences) -> Preferences:
        await self._store.put(caller.tenant_id, caller.user_id, preferences)
        return preferences

    async def instructions_for(self, caller: Caller) -> str:
        return (await self.get(caller)).instructions()

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        return await self._store.erase(tenant_id, user_id)


def preferences(container: Container) -> PreferencesService:
    """Provider: how the container builds this feature's service."""
    return PreferencesService(PreferencesStore(container.database))
