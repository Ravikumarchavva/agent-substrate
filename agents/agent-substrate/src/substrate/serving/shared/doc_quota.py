"""Daily per-user document quotas — fixed-window Redis counters.

Distinct from ``rate_limit.py``'s sliding-window ZSET (built for smoothing
bursty HTTP request rates over short windows). A once-a-day cap doesn't need
sub-window smoothing, so this follows the simpler ``INCR``/``EXPIRE`` idiom
already used for the refresh-token JTI store in ``routes/auth.py``.

Two independent counters share this helper (see ``routes/files.py`` and
``routes/chat_context.py``):

  docquota:upload:{sub}  — raw upload attempts/day (coarse abuse guard;
                            eager extraction starts unconditionally on
                            upload, so this bounds worst-case compute from
                            repeated upload-then-discard).
  docquota:commit:{sub}  — documents actually *sent* in a chat
                            message/day (the real user-facing limit).

Usage::

    from substrate.serving.shared.doc_quota import check_and_increment

    allowed, remaining = await check_and_increment(
        request.app.state.redis, "docquota:commit", claims.sub, limit=20,
    )
    if not allowed:
        raise HTTPException(429, "Daily document limit reached")
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _key(key_prefix: str, user_id: str) -> str:
    return f"{key_prefix}:{user_id}:{datetime.now(timezone.utc).date().isoformat()}"


def seconds_until_reset() -> int:
    """Seconds until the UTC-day bucket rolls over — matches _key's date
    boundary, so a status display's "resets in" is never off from when the
    counter this session actually resets."""
    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).date()
    midnight = datetime(
        tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=timezone.utc
    )
    return max(0, int((midnight - now).total_seconds()))


async def check_and_increment(
    redis,
    key_prefix: str,
    user_id: str,
    limit: int,
    *,
    count: int = 1,
) -> tuple[bool, int]:
    """Atomically add *count* to today's counter for *user_id*.

    Returns ``(allowed, remaining_after)`` — ``allowed`` is ``False`` when
    this increment pushed the total over *limit* (the increment still
    happened; callers that need to "give back" quota on a failed downstream
    step should call ``release`` below).
    """
    key = _key(key_prefix, user_id)
    total = await redis.incrby(key, count)
    if total == count:
        # First increment for this key today — set the day-long TTL once,
        # not on every call (re-setting it each time would keep pushing the
        # expiry forward and the key would never actually reset daily).
        await redis.expire(key, 86400)
    return total <= limit, max(0, limit - total)


async def release(redis, key_prefix: str, user_id: str, *, count: int = 1) -> None:
    """Give back *count* units — for a commit that was provisionally counted
    then failed downstream. Never lets the counter go negative."""
    key = _key(key_prefix, user_id)
    total = await redis.decrby(key, count)
    if total < 0:
        await redis.set(key, 0, keepttl=True)


async def peek(redis, key_prefix: str, user_id: str, limit: int) -> tuple[int, int]:
    """Read-only: ``(used, remaining)`` for today, without incrementing."""
    key = _key(key_prefix, user_id)
    raw = await redis.get(key)
    used = int(raw) if raw else 0
    return used, max(0, limit - used)


__all__ = ["check_and_increment", "release", "peek", "seconds_until_reset"]
