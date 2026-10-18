"""``GET /health`` says the process is up (cheap, for a load balancer's liveness probe); ``GET /health/ready`` says it can do its job.

Ready checks the things a request needs: the application database, the engine's store, Redis (when configured) and the scheduler. Each is
reported with how long it took and, if it failed, a short reason that is safe to show; the status is 503 when anything required is down,
so an orchestrator stops sending traffic to an instance that cannot serve it. Nothing here needs a sign-in, so nothing here may reveal
more than "this part is down".
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

router = APIRouter(prefix="/health", tags=["infra"])

CHECK_TIMEOUT_S = 3.0


async def _timed(name: str, check: Callable[[], Awaitable[Any]]) -> dict[str, Any]:
    started = time.monotonic()
    try:
        await asyncio.wait_for(check(), timeout=CHECK_TIMEOUT_S)
        return {
            "name": name,
            "ok": True,
            "ms": round((time.monotonic() - started) * 1000),
        }
    except TimeoutError:
        error = "timed out"
    except Exception:  # noqa: BLE001 — the reason is for operators' logs, not the response
        error = "unavailable"
    return {
        "name": name,
        "ok": False,
        "ms": round((time.monotonic() - started) * 1000),
        "error": error,
    }


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    state = request.app.state
    checks: list[Awaitable[dict[str, Any]]] = []

    async def database() -> None:
        async with state.session_factory() as db:
            await db.execute(text("SELECT 1"))

    async def store() -> None:
        async def op(tx: Any) -> None:
            await tx.fetchall("SELECT 1 AS ok")

        await state.store.run(op)

    checks += [_timed("database", database), _timed("store", store)]

    redis = getattr(state, "redis", None)
    if redis is not None:
        checks.append(_timed("redis", redis.ping))

    scheduler = getattr(state, "trigger_scheduler", None)

    async def scheduling() -> None:
        if scheduler is None or getattr(scheduler, "_scheduler", None) is None:
            raise RuntimeError("scheduler is not running")

    checks.append(_timed("scheduler", scheduling))

    results = await asyncio.gather(*checks)
    healthy = all(r["ok"] for r in results)
    return JSONResponse(
        {"status": "ready" if healthy else "unavailable", "checks": results},
        status_code=200 if healthy else 503,
        headers={"Cache-Control": "no-store"},
    )
