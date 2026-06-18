"""Rate-limit status endpoint — read-only, no increment."""

from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Request

from ravi.serving.shared.auth.claims import AuthClaims
from ravi.serving.shared.auth.middleware import optional_current_user
from fastapi import Depends

router = APIRouter(prefix="/rate-limit", tags=["rate-limit"])


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


@router.get("/status")
async def rate_limit_status(
    request: Request,
    user: Optional[AuthClaims] = Depends(optional_current_user),
):
    """Return current rate-limit usage for this caller without incrementing.

    Response:
      enabled        — whether rate limiting is active
      used           — requests made in the current window
      limit          — max requests allowed in the window
      window_seconds — window size
      reset_in       — seconds until the oldest entry falls out of the window
    """
    settings = getattr(request.app.state, "rate_limit_settings", None)
    redis = getattr(request.app.state, "redis", None)

    if settings is None or not settings.get("enabled", True):
        return {
            "enabled": False,
            "used": 0,
            "limit": 0,
            "window_seconds": 60,
            "reset_in": 0,
        }

    if user is not None:
        limit: int = settings.get("authed_rpm", 60)
        key = f"rl:user:{user.sub}"
    else:
        limit = settings.get("anon_rpm", 5)
        ip = _client_ip(request)
        key = f"rl:ip:{ip}"

    window_seconds: int = settings.get("window_seconds", 60)

    if redis is None:
        return {
            "enabled": True,
            "used": 0,
            "limit": limit,
            "window_seconds": window_seconds,
            "reset_in": window_seconds,
        }

    try:
        now = time.time()
        window_start = now - window_seconds

        pipe = redis.pipeline()
        pipe.zremrangebyscore(key, "-inf", window_start)
        pipe.zcard(key)
        pipe.zrange(key, 0, 0, withscores=True)  # oldest entry timestamp
        results = await pipe.execute()

        used: int = results[1]
        oldest = results[2]

        if oldest:
            oldest_ts: float = oldest[0][1]
            reset_in = max(0, int(window_seconds - (now - oldest_ts)) + 1)
        else:
            reset_in = window_seconds

    except Exception:
        return {
            "enabled": True,
            "used": 0,
            "limit": limit,
            "window_seconds": window_seconds,
            "reset_in": window_seconds,
        }

    return {
        "enabled": True,
        "used": used,
        "limit": limit,
        "window_seconds": window_seconds,
        "reset_in": reset_in,
    }
