"""FastAPI dependencies every feature router uses: the container, the verified caller, rate limiting and a feature's service."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, Request

from loamz.agents.bootstrap.container import Container, Provider
from loamz.agents.kernel.caller import Caller
from loamz.data.storage.errors import (
    ForbiddenError,
    RateLimitedError,
    ServiceUnavailableError,
    UnauthenticatedError,
)
from loamz.identity import TokenVerifier

logger = logging.getLogger(__name__)


def container(request: Request) -> Container:
    return request.app.state.container


async def _verified(request: Request) -> Caller:
    """The caller the bearer token proves, verified once per request."""
    cached: Caller | None = getattr(request.state, "caller", None)
    if cached is not None:
        return cached
    verifier: TokenVerifier | None = request.app.state.verifier
    if verifier is None:
        raise UnauthenticatedError(
            "Sign-in is not configured on this server (LOAMZ_AGENTS_OIDC__ISSUER)."
        )
    who = await verifier.averify(request.headers.get("authorization"))
    if not who.tenant_id:
        raise ForbiddenError("This token names no organization; choose one when signing in.")
    request.state.caller = Caller(
        tenant_id=who.tenant_id,
        user_id=who.subject,
        kind=who.kind,
        roles=who.roles,
        scopes=who.scopes,
        email=who.email,
        name=who.name,
    )
    return request.state.caller


async def caller(request: Request) -> Caller:
    """The verified caller of an agents route. A delegated token (one limited to data scopes) has no business here."""
    who = await _verified(request)
    if who.scopes is not None:
        raise ForbiddenError("A delegated token cannot call the Agents API.")
    return who


async def optional_caller(request: Request) -> Caller | None:
    """The caller if the request has a valid token; None for an anonymous request (public routes)."""
    if not request.headers.get("authorization"):
        return None
    try:
        return await caller(request)
    except UnauthenticatedError, ForbiddenError:
        return None
    except Exception:  # noqa: BLE001 - a bad token on a public route is just anonymous
        return None


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_key(request: Request, who: Caller | None) -> tuple[str, str]:
    """`(counter key, tier)`: a person by tenant and user, an anonymous client by address."""
    if who is not None:
        return f"rl:user:{who.tenant_id}:{who.user_id}", "authenticated"
    return f"rl:ip:{client_ip(request)}", "anonymous"


async def rate_limit(
    request: Request,
    c: Annotated[Container, Depends(container)],
    who: Annotated[Caller | None, Depends(optional_caller)],
) -> None:
    """Count this request in the caller's sliding window; 429 once the tier's limit is passed."""
    cfg = c.settings.rate_limit
    if not cfg.enabled:
        return
    key, tier = rate_key(request, who)
    limit = cfg.authed_rpm if who is not None else cfg.anon_rpm
    try:
        window = await c.counters.hit(key, window_seconds=cfg.window_seconds)
    except Exception:  # noqa: BLE001 - the counters' store is down
        if cfg.fail_open:
            logger.exception("rate limit: counters unavailable, letting the request through")
            return
        logger.exception("rate limit: counters unavailable, refusing")
        raise ServiceUnavailableError("Rate limiter unavailable. Try again shortly.") from None
    request.state.rate_headers = {
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Remaining": str(max(0, limit - window.count)),
        "X-RateLimit-Reset": str(int(time.time()) + window.reset_in),
        "X-RateLimit-Window": str(cfg.window_seconds),
    }
    if window.count > limit:
        logger.warning("rate limit: %s exceeded (%d/%d)", tier, window.count, limit)
        raise RateLimitedError(
            f"Rate limit exceeded. Try again in {window.reset_in} seconds.",
            retry_after=window.reset_in,
        )


def use[T](provider: Provider[T]) -> Callable[..., Any]:
    """A dependency that resolves a feature's service: `Annotated[PreferencesService, Depends(use(preferences))]`."""

    def resolve(c: Container = Depends(container)) -> T:
        return c.get(provider)

    return resolve


ContainerDep = Annotated[Container, Depends(container)]
CallerDep = Annotated[Caller, Depends(caller)]
OptionalCallerDep = Annotated[Caller | None, Depends(optional_caller)]
RateLimit = Depends(rate_limit)
