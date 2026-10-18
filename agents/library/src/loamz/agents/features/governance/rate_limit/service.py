from __future__ import annotations

from loamz.agents.bootstrap.container import Container
from loamz.agents.bootstrap.settings import RateLimitSettings
from loamz.agents.kernel.ports.counters import Counters

__all__ = ["RateLimitService", "rate_limit"]


class RateLimitService:
    """Where a caller stands against the request limit, read without counting the call."""

    def __init__(self, counters: Counters, settings: RateLimitSettings) -> None:
        self._counters = counters
        self._cfg = settings

    async def status(self, key: str, *, authenticated: bool) -> dict[str, int | bool]:
        """`enabled`, `used`, `limit`, `window_seconds`, `reset_in` for the window keyed by `key`."""
        cfg = self._cfg
        if not cfg.enabled:
            return {
                "enabled": False,
                "used": 0,
                "limit": 0,
                "window_seconds": cfg.window_seconds,
                "reset_in": 0,
            }
        limit = cfg.authed_rpm if authenticated else cfg.anon_rpm
        try:
            window = await self._counters.peek(key, window_seconds=cfg.window_seconds)
            used, reset_in = window.count, window.reset_in
        except Exception:  # noqa: BLE001 - the counters' store is down: report an empty window, never fail a status call
            used, reset_in = 0, cfg.window_seconds
        return {
            "enabled": True,
            "used": used,
            "limit": limit,
            "window_seconds": cfg.window_seconds,
            "reset_in": reset_in,
        }

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        """Windows are short-lived counters, not personal data: nothing is kept."""
        return 0


def rate_limit(container: Container) -> RateLimitService:
    """Provider: how the container builds this feature's service."""
    return RateLimitService(container.counters, container.settings.rate_limit)
