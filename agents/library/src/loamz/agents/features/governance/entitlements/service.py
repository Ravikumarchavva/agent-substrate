from __future__ import annotations

from loamz.agents.bootstrap.container import Container
from loamz.agents.features.governance.entitlements.model import Entitlements, EntitlementsIn
from loamz.agents.features.governance.entitlements.store import EntitlementStore
from loamz.agents.kernel.caller import Caller
from loamz.agents.kernel.ports.counters import Counters, seconds_until_tomorrow
from loamz.data.storage.errors import ForbiddenError, InvalidRequestError, RateLimitedError
from loamz.identity.tenancy import PROVISION_TENANT_ROLE, TenancyError, check_tenant_id

PLATFORM_ADMIN_ROLE = "platform_admin"

__all__ = ["Entitlements", "EntitlementsService", "entitlements"]


def plan_quota_key(tenant_id: str) -> str:
    """The daily message counter of a tenant: every member shares one."""
    return f"planquota:message:{tenant_id}"


class EntitlementsService:
    """The limits the platform sets (through cell-admin) and this cell enforces.

    Other features read `daily_message_limit` / `entitlements` and call `enforce_daily_messages` before a chat turn.
    """

    def __init__(self, store: EntitlementStore, counters: Counters) -> None:
        self._store = store
        self._counters = counters

    async def entitlements(self, tenant_id: str) -> Entitlements:
        """The tenant's entitlements; unlimited defaults when nothing was configured."""
        return await self._store.get(tenant_id) or Entitlements(tenant_id=tenant_id)

    async def daily_message_limit(self, tenant_id: str) -> int | None:
        return (await self.entitlements(tenant_id)).daily_message_limit

    async def mine(self, caller: Caller) -> Entitlements:
        return await self.entitlements(caller.tenant_id)

    async def put(self, caller: Caller, tenant_id: str, body: EntitlementsIn) -> Entitlements:
        """The platform (a platform administrator or the cell-admin service account) configures a tenant."""
        if not caller.has_role(PLATFORM_ADMIN_ROLE, PROVISION_TENANT_ROLE):
            raise ForbiddenError("Only the platform (cell-admin) may change entitlements")
        try:
            check_tenant_id(tenant_id)
        except TenancyError as exc:
            raise InvalidRequestError(str(exc)) from exc
        saved = Entitlements(
            tenant_id=tenant_id,
            daily_message_limit=body.daily_message_limit,
            features=body.features,
        )
        await self._store.put(saved, caller.user_id)
        return saved

    async def enforce_daily_messages(self, tenant_id: str) -> None:
        """Count one message against the tenant's daily limit; `RateLimitedError` once it is spent. No limit: no count."""
        limit = await self.daily_message_limit(tenant_id)
        if limit is None:
            return
        day = await self._counters.add_day(plan_quota_key(tenant_id), limit=limit)
        if not day.allowed:
            await self._counters.release_day(plan_quota_key(tenant_id))
            wait = seconds_until_tomorrow()
            raise RateLimitedError(
                f"Daily message limit ({limit}) reached. Resets in {wait} seconds.",
                retry_after=wait,
            )

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        """Entitlements are the platform's commercial settings, not the tenant's personal data: erasure leaves them."""
        return 0


def entitlements(container: Container) -> EntitlementsService:
    """Provider: how the container builds this feature's service."""
    return EntitlementsService(EntitlementStore(container.database), container.counters)
