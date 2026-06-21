"""HTTP-layer rate limiter — Redis sliding-window, two-tier (authed vs. anonymous).

Sliding window algorithm (ZSET-based):
  Every request is recorded as a scored entry (score = timestamp).
  To check the limit, we count entries in the last ``window_seconds``.
  Old entries are pruned atomically in the same pipeline.
  This avoids the fixed-window edge burst (double-rate at the boundary).

Key scheme:
  rl:user:{sub}:{window_tag}        — authenticated
  rl:ip:{ip}:{window_tag}          — anonymous

``window_tag`` is the current minute/hour bucket — used only to bound the
ZSET's natural expiry (it would grow forever without a TTL).

Usage (FastAPI dependency)::

    from substrate.serving.shared.rate_limit import rate_limit

    @router.post("/chat")
    async def chat(
        body: ChatRequest,
        request: Request,
        _rl: None = Depends(rate_limit),   # anonymous-aware, auth-optional
        user: AuthClaims = Depends(get_current_user),
    ):
        ...

Or with explicit auth::

    _rl: None = Depends(rate_limit_for(user))
"""

from __future__ import annotations

import math
import time
import uuid
from typing import Optional

from fastapi import Depends, HTTPException, Request, status

from substrate.logger import setup_logging
from substrate.serving.shared.auth.claims import AuthClaims
from substrate.serving.shared.auth.middleware import optional_current_user

logger = setup_logging()


def _client_ip(request: Request) -> str:
    """Best-effort real IP, honouring X-Forwarded-For from trusted proxies."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


async def _sliding_window_check(
    redis,
    key: str,
    *,
    limit: int,
    window_seconds: int,
) -> tuple[int, int]:
    """Increment the sliding-window counter and return (count, reset_in_seconds).

    Uses a Redis ZSET pipeline:
      1. Remove entries older than the window.
      2. Add the current request (unique member = timestamp+random suffix).
      3. Count remaining entries.
      4. Set TTL so the key expires naturally.

    Returns (current_count, seconds_until_window_resets).
    """
    now = time.time()
    window_start = now - window_seconds
    member = f"{now:.6f}:{uuid.uuid4().hex[:8]}"

    pipe = redis.pipeline()
    pipe.zremrangebyscore(key, "-inf", window_start)
    pipe.zadd(key, {member: now})
    pipe.zcard(key)
    pipe.expire(key, window_seconds + 1)
    results = await pipe.execute()

    count: int = results[2]
    reset_in = window_seconds  # worst-case reset
    return count, reset_in


async def _check(
    request: Request,
    user: Optional[AuthClaims],
) -> None:
    """Core rate-limit check. Raises HTTP 429 when the limit is exceeded."""
    redis = getattr(request.app.state, "redis", None)
    settings = getattr(request.app.state, "rate_limit_settings", None)

    # If not configured or disabled, pass through
    if settings is None or not settings.get("enabled", True):
        return
    if redis is None:
        logger.warning("rate_limit: Redis not available on app.state — skipping")
        return

    if user is not None:
        limit = settings.get("authed_rpm", 60)
        window = settings.get("window_seconds", 60)
        key = f"rl:user:{user.sub}"
        tier = "authenticated"
    else:
        limit = settings.get("anon_rpm", 5)
        window = settings.get("window_seconds", 60)
        ip = _client_ip(request)
        key = f"rl:ip:{ip}"
        tier = f"anonymous ({ip})"

    try:
        count, reset_in = await _sliding_window_check(
            redis, key, limit=limit, window_seconds=window
        )
    except Exception:
        logger.exception("rate_limit: Redis error — skipping limit check")
        return

    remaining = max(0, limit - count)
    reset_ts = math.ceil(time.time()) + reset_in

    # Attach rate-limit headers to the response (informational)
    request.state.rl_headers = {
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Reset": str(reset_ts),
        "X-RateLimit-Window": str(window),
    }

    if count > limit:
        logger.warning(
            "rate_limit: %s exceeded (%d/%d in %ds window)", tier, count, limit, window
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded. Try again in {reset_in} seconds.",
            headers={
                "Retry-After": str(reset_in),
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(reset_ts),
            },
        )


async def rate_limit(
    request: Request,
    user: Optional[AuthClaims] = Depends(optional_current_user),
) -> None:
    """FastAPI dependency: rate-limit by sub claim (authed) or IP (anon).

    Wire into any route or router::

        router = APIRouter(dependencies=[Depends(rate_limit)])
    """
    await _check(request, user)


def rate_limit_settings(
    *,
    enabled: bool = True,
    authed_rpm: int = 60,
    anon_rpm: int = 5,
    window_seconds: int = 60,
) -> dict:
    """Build the settings dict stored on ``app.state.rate_limit_settings``."""
    return {
        "enabled": enabled,
        "authed_rpm": authed_rpm,
        "anon_rpm": anon_rpm,
        "window_seconds": window_seconds,
    }
