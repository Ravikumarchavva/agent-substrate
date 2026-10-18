from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import ClassVar

from loamz.agents.bootstrap.container import Container
from loamz.data.storage.errors import ServiceUnavailableError

CHECK_TIMEOUT_S = 3.0

__all__ = ["HealthService", "NotReadyError", "health"]


class NotReadyError(ServiceUnavailableError):
    """A required dependency is down. The problem document carries each check, never the reason behind a failure."""

    code: ClassVar[str] = "unavailable"

    def __init__(self, checks: list[dict[str, object]]) -> None:
        super().__init__(
            "Not ready: " + ", ".join(str(c["name"]) for c in checks if not c.get("ok"))
        )
        self.checks = checks

    def extensions(self) -> dict[str, object]:
        return {"status": "unavailable", "checks": self.checks}


async def _timed(name: str, check: Callable[[], Awaitable[object]]) -> dict[str, object]:
    started = time.monotonic()
    try:
        await asyncio.wait_for(check(), timeout=CHECK_TIMEOUT_S)
        return {"name": name, "ok": True, "ms": round((time.monotonic() - started) * 1000)}
    except TimeoutError:
        error = "timed out"
    except Exception:  # noqa: BLE001 - the reason is for operators' logs, not the response
        error = "unavailable"
    return {
        "name": name,
        "ok": False,
        "ms": round((time.monotonic() - started) * 1000),
        "error": error,
    }


class HealthService:
    """Readiness: the things a request needs. Nothing here needs a sign-in, so nothing here may reveal more than
    "this part is down"."""

    def __init__(self, container: Container) -> None:
        self._c = container

    async def ready(self) -> dict[str, object]:
        c = self._c

        async def database() -> None:
            async with c.database.transaction() as tx:
                await tx.fetchall("SELECT 1 AS ok")

        async def file_store() -> None:
            await c.file_store.exists("tenants/.ready")

        async def event_bus() -> None:
            url = c.settings.redis.url
            if not url:
                return  # the in-process bus cannot be down while this process answers
            import redis.asyncio as aioredis

            client = aioredis.from_url(url, socket_connect_timeout=CHECK_TIMEOUT_S)
            try:
                await client.ping()
            finally:
                await client.aclose()

        results = await asyncio.gather(
            _timed("database", database),
            _timed("file_store", file_store),
            _timed("event_bus", event_bus),
        )
        if not all(r["ok"] for r in results):
            raise NotReadyError(list(results))
        return {"status": "ready", "checks": list(results)}

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        return 0


def health(container: Container) -> HealthService:
    """Provider: how the container builds this feature's service."""
    return HealthService(container)
