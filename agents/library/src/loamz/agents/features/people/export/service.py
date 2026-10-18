"""`GET /me/export`: everything the account holds about the caller, as one JSON document ("download my data").

Their conversations (what was said), memories, preferences and notifications, plus a section from every other feature
whose service offers `async export_data(caller) -> object` (found through the registry, like erasure). File contents are
not included: they are in storage, where they can be downloaded one by one. Permanent erasure is a separate, deliberate
action (`governance.gdpr`); this only reads.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from loamz.agents.bootstrap.container import Container
from loamz.agents.features.people.memory.service import memory
from loamz.agents.features.people.notifications.service import notifications
from loamz.agents.features.people.preferences.service import preferences
from loamz.agents.kernel.caller import Caller
from loamz.agents.kernel.context.history import project_messages
from loamz.agents.kernel.types.content import Role
from loamz.data.storage.errors import RateLimitedError

MAX_THREADS = 500
MIN_GAP_S = 60
"""Building this reads every conversation: once a minute per person is plenty and keeps it from loading the server."""

__all__ = ["ExportService", "export"]


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class ExportService:
    def __init__(self, container: Container) -> None:
        self._c = container

    async def _conversations(self, caller: Caller) -> list[dict[str, Any]]:
        store = self._c.agent_store
        threads = store.tenant(caller.tenant_id).threads
        ids = await store.thread_headers.owned_ids(
            caller.tenant_id, caller.user_id, limit=MAX_THREADS
        )
        headers = [
            await store.thread_headers.get_owned(caller.tenant_id, caller.user_id, i) for i in ids
        ]
        out = []
        for header in sorted(filter(None, headers), key=lambda h: h.created_at):
            messages = [
                {"role": m.role.value, "text": m.text}
                for m in await project_messages(threads, header.id)
                if m.role in (Role.USER, Role.ASSISTANT) and m.text.strip()
            ]
            out.append(
                {
                    "id": header.id,
                    "title": header.name,
                    "created_at": _iso(header.created_at),
                    "archived": header.archived_at is not None,
                    "messages": messages,
                }
            )
        return out

    async def _sections(self, caller: Caller) -> dict[str, Any]:
        from loamz.agents.features import FEATURES  # lazily: the registry imports every feature

        sections: dict[str, Any] = {}
        for feature in FEATURES:
            service = feature.service(self._c)
            export_data = getattr(service, "export_data", None)
            if export_data is not None:
                sections[feature.name] = await export_data(caller)
        return sections

    async def export(self, caller: Caller) -> dict[str, Any]:
        window = await self._c.counters.hit(
            f"export:{caller.tenant_id}:{caller.user_id}", window_seconds=MIN_GAP_S
        )
        if window.count > 1:
            raise RateLimitedError(
                "Your data was just exported. Try again in a minute.", retry_after=window.reset_in
            )
        c = self._c
        prefs = await c.get(preferences).get(caller)
        notes = await c.get(notifications).recent(caller)
        return {
            "exported_at": datetime.now(UTC).isoformat(),
            "account": {"id": caller.user_id, "email": caller.email},
            "preferences": prefs.model_dump(),
            "memories": await c.get(memory).texts(caller),
            "conversations": await self._conversations(caller),
            "notifications": [
                {"title": n.title, "body": n.body, "at": _iso(n.created_at)} for n in notes
            ],
            "other": await self._sections(caller),
            "note": "File contents are not included; download them from Storage.",
        }

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        """Nothing is kept: an export is built on request."""
        return 0


def export(container: Container) -> ExportService:
    """Provider: how the container builds this feature's service."""
    return ExportService(container)
