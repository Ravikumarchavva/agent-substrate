from __future__ import annotations

from loamz.engine.agents.kernel.caller import Caller
from loamz.engine.errors import NotFoundError
from loamz.server.agents.bootstrap.container import Container
from loamz.server.agents.features.governance.workspace_oauth.model import (
    SetWorkspaceToken,
    WorkspaceToken,
)
from loamz.server.agents.features.governance.workspace_oauth.store import WorkspaceTokenStore

__all__ = ["WorkspaceOAuthService", "WorkspaceToken", "workspace_oauth"]


class WorkspaceOAuthService:
    """A short-lived cache of a person's Google Workspace access token.

    The frontend owns the OAuth dance (code exchange, refresh) and its durable store; this only saves repeated backend
    calls (e.g. the `google_workspace` tool) a round-trip. An empty or expired cache is not an error for the frontend:
    it falls back to its own store and re-populates this one.

    TODO(integration): encrypt at rest / secret store (the Redis cache it replaces held it in plain text too).
    """

    def __init__(self, store: WorkspaceTokenStore) -> None:
        self._store = store

    async def get(self, caller: Caller) -> WorkspaceToken:
        token = await self._store.get(caller.tenant_id, caller.user_id)
        if token is None:
            raise NotFoundError("No cached Google Workspace token for this user")
        return WorkspaceToken(access_token=token)

    async def token_for(self, tenant_id: str, user_id: str) -> str | None:
        """For tools: the person's cached access token, or None."""
        return await self._store.get(tenant_id, user_id)

    async def set(self, caller: Caller, body: SetWorkspaceToken) -> None:
        await self._store.put(
            caller.tenant_id, caller.user_id, body.access_token, max(1, body.expires_in)
        )

    async def clear(self, caller: Caller) -> None:
        await self._store.erase(caller.tenant_id, caller.user_id)

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        return await self._store.erase(tenant_id, user_id)


def workspace_oauth(container: Container) -> WorkspaceOAuthService:
    """Provider: how the container builds this feature's service."""
    return WorkspaceOAuthService(WorkspaceTokenStore(container.database))
