from __future__ import annotations

import logging
import time

from loamz.agents.bootstrap.container import Container
from loamz.agents.features.governance.connector_tokens.model import TokenPush
from loamz.agents.features.governance.connector_tokens.store import ConnectorTokenStore
from loamz.agents.kernel.caller import Caller

logger = logging.getLogger(__name__)

GRACE_S = 300
MIN_TTL_S = 60

__all__ = ["ConnectorTokenService", "TokenPush", "connector_tokens"]


class ConnectorTokenService:
    """OAuth tokens of connectors. The platform owns OAuth initiation, callbacks and the durable credential; after a
    successful callback it pushes the token here so the agent's tools can use it without a round-trip back.

    TODO(integration): encrypt at rest / keep in the platform's secret store; today the row is plain text with a TTL
    (as the Redis cache it replaces was).
    """

    def __init__(self, store: ConnectorTokenStore) -> None:
        self._store = store

    async def push(self, caller: Caller, connector_type: str, token: TokenPush) -> None:
        """Store a token for the caller's tenant; it lives until its expiry plus a five-minute grace (at least a minute)."""
        ttl = max(token.expires_at - int(time.time()) + GRACE_S, MIN_TTL_S)
        await self._store.put(caller.tenant_id, connector_type, caller.user_id, token, ttl)
        logger.info("Stored %s token for project %s", connector_type, token.project_id)

    async def delete(self, caller: Caller, connector_type: str, project_id: str) -> None:
        await self._store.delete(caller.tenant_id, connector_type, project_id)
        logger.info("Deleted %s token for project %s", connector_type, project_id)

    async def get(self, tenant_id: str, connector_type: str, project_id: str) -> TokenPush | None:
        """For tools: the live token of a project, or None."""
        return await self._store.get(tenant_id, connector_type, project_id)

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        return await self._store.erase(tenant_id, user_id)


def connector_tokens(container: Container) -> ConnectorTokenService:
    """Provider: how the container builds this feature's service."""
    return ConnectorTokenService(ConnectorTokenStore(container.database))
