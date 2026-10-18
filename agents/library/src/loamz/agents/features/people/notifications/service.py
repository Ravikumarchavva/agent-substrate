from __future__ import annotations

import logging

from loamz.agents.bootstrap.container import Container
from loamz.agents.bootstrap.settings import NotificationSettings
from loamz.agents.features.people.notifications.model import (
    MAX_BODY_CHARS,
    MAX_TITLE_CHARS,
    Notification,
    NotificationPage,
)
from loamz.agents.features.people.notifications.store import NotificationStore
from loamz.agents.kernel.caller import Caller
from loamz.data.storage.errors import NotFoundError

logger = logging.getLogger(__name__)

EMAIL_TIMEOUT_S = 10.0
RESEND_URL = "https://api.resend.com/emails"

__all__ = ["Notification", "NotificationPage", "NotificationService", "notifications"]


class NotificationService:
    """Telling a person about things that happened while they were away.

    A notification is a row (shown in the app's notification centre) and, when the caller asks, an e-mail. Both are
    best effort for the code that causes them: `email` never raises. Other features (automation) call `notify`.
    """

    def __init__(self, store: NotificationStore, settings: NotificationSettings) -> None:
        self._store = store
        self._settings = settings

    # ── for other features ──────────────────────────────────────────────────
    async def notify(
        self,
        tenant_id: str,
        user_id: str,
        title: str,
        body: str = "",
        link: str | None = None,
        kind: str = "info",
        thread_id: str | None = None,
    ) -> Notification:
        """Record a notification for one person."""
        return await self._store.add(
            tenant_id,
            user_id,
            kind=kind,
            title=title[:MAX_TITLE_CHARS],
            body=body[:MAX_BODY_CHARS],
            thread_id=thread_id,
            link=link,
        )

    async def email(self, to: str, subject: str, text: str, client: object | None = None) -> bool:
        """Send one plain-text e-mail through Resend. `False` (never an exception) with no key, no address or a failed send."""
        key = self._settings.resend_api_key
        if not key or not to:
            return False
        try:
            import httpx

            async with client or httpx.AsyncClient(timeout=EMAIL_TIMEOUT_S) as http:  # type: ignore[attr-defined]
                response = await http.post(
                    RESEND_URL,
                    headers={"Authorization": f"Bearer {key}"},
                    json={
                        "from": self._settings.from_email,
                        "to": [to],
                        "subject": subject[:200],
                        "text": text,
                    },
                )
            if response.status_code >= 300:
                logger.warning("email to %s was refused: HTTP %s", to, response.status_code)
                return False
            return True
        except Exception as exc:  # noqa: BLE001 - notification is best effort
            logger.warning("email to %s failed: %s", to, exc)
            return False

    # ── the notification centre ─────────────────────────────────────────────
    async def list(
        self, caller: Caller, *, page_size: int = 50, page_token: str | None = None
    ) -> NotificationPage:
        items, next_token, unread = await self._store.list(
            caller.tenant_id,
            caller.user_id,
            page_size=max(1, min(page_size, 200)),
            page_token=page_token,
        )
        return NotificationPage(items=items, next_page_token=next_token, unread=unread)

    async def mark_read(self, caller: Caller, notification_id: str) -> None:
        if not await self._store.mark_read(caller.tenant_id, caller.user_id, notification_id):
            raise NotFoundError("Notification not found")

    async def mark_all_read(self, caller: Caller) -> None:
        await self._store.mark_all_read(caller.tenant_id, caller.user_id)

    async def recent(self, caller: Caller, limit: int = 200) -> list[Notification]:
        """The newest notifications (for the data export)."""
        return (await self.list(caller, page_size=limit)).items

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        return await self._store.erase(tenant_id, user_id)


def notifications(container: Container) -> NotificationService:
    """Provider: how the container builds this feature's service."""
    return NotificationService(
        NotificationStore(container.database), container.settings.notifications
    )
