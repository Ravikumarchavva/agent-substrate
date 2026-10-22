"""Fair use: request rate, concurrent statements and a daily scan budget, per principal.

The point is protection of shared compute from one runaway client or agent, not billing. Request
rate and concurrency are tracked in this API process (each replica enforces its own share); the
daily budget lives in the catalog so every replica sees the same total.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from datetime import UTC, datetime, timedelta

from loamz.engine.errors import RateLimitedError
from loamz.server.data.kernel.settings import Settings

MAX_TRACKED = 20_000


class RateLimiter:
    """A token bucket per principal: `limit` requests a minute, with a burst of a sixth of that."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.lock = threading.Lock()
        self.buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()

    def check(self, principal: str) -> None:
        limit = self.settings.rate_limit_per_minute
        if limit <= 0:
            return
        rate, burst = limit / 60.0, max(10.0, limit / 6.0)
        now = time.monotonic()
        with self.lock:
            tokens, stamp = self.buckets.pop(principal, (burst, now))
            tokens = min(burst, tokens + (now - stamp) * rate)
            if tokens < 1:
                self.buckets[principal] = (tokens, now)
                wait = max(1, int((1 - tokens) / rate) + 1)
                raise RateLimitedError(f"Too many requests; retry in {wait} s", retry_after=wait)
            self.buckets[principal] = (tokens - 1, now)
            while len(self.buckets) > MAX_TRACKED:
                self.buckets.popitem(last=False)


def seconds_until_midnight_utc() -> int:
    now = datetime.now(UTC)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((tomorrow - now).total_seconds()))


def check_concurrency(
    active: object, settings: Settings, principal: str, tenant: str | None
) -> None:
    """Refuse a new statement when the principal (or their tenant) already has too many running."""
    own = settings.max_concurrent_statements
    if own and active.count(principal) >= own:
        raise RateLimitedError(
            f"You already have {own} statements running; wait for one to finish", retry_after=1
        )
    shared = settings.max_concurrent_statements_per_tenant
    if shared and tenant and active.count_tenant(tenant) >= shared:
        raise RateLimitedError(
            f"Your tenant already has {shared} statements running; retry shortly", retry_after=1
        )


def check_budget(store: object, settings: Settings, principal: str) -> None:
    budget = settings.daily_scan_bytes_per_principal
    if budget and store.usage(principal)["bytes_planned"] >= budget:
        raise RateLimitedError(
            f"The daily scan budget of {budget} bytes is used up; it resets at 00:00 UTC",
            retry_after=seconds_until_midnight_utc(),
        )
