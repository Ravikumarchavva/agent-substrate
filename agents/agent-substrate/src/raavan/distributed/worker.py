"""Standalone Restate worker — ``python -m raavan.distributed.worker``.

Lifecycle:
1. **Setup** — create model client, NATS bridge, Redis memory, scan
   tools, call ``activities.configure()``.
2. **Register** — POST deployment URL to Restate admin.
3. **Serve** — run the Restate ASGI app via uvicorn.
4. **Teardown** — disconnect NATS, Redis on SIGTERM.

Environment variables (from ``configs/settings.py``)::

    OPENAI_API_KEY      — required
    NATS_URL            — default nats://localhost:4222
    REDIS_URL           — default redis://localhost:6379/0
    RESTATE_ADMIN_URL   — default http://localhost:9070
    SYSTEM_INSTRUCTIONS — optional per-agent system prompt
"""

from __future__ import annotations

import asyncio
import logging
import signal
from typing import Any, Dict

import uvicorn

from raavan.configs.settings import settings

logger = logging.getLogger(__name__)

# ── Module-level handles for teardown ────────────────────────────────────
_nats: Any = None
_redis_memory: Any = None


async def _setup() -> Dict[str, Any]:
    """Initialise all dependencies and configure activities.

    Returns a dict of live objects for teardown.
    """
    global _nats, _redis_memory

    from raavan.distributed import activities
    from raavan.distributed.client import RestateClient
    from raavan.distributed.streaming import NATSStreamingBridge
    from raavan.integrations.llm.openai.openai_client import OpenAIClient
    from raavan.integrations.memory.redis_memory import RedisMemory

    # ── Model client ─────────────────────────────────────────────────
    model_client = OpenAIClient(api_key=settings.OPENAI_API_KEY)

    # ── NATS streaming bridge ────────────────────────────────────────
    _nats = NATSStreamingBridge(nats_url=settings.NATS_URL)
    await _nats.connect()
    logger.info("Connected to NATS at %s", settings.NATS_URL)

    # ── Redis memory (connection pool) ───────────────────────────────
    _redis_memory = RedisMemory(
        session_id="worker-pool",
        redis_url=settings.REDIS_URL,
    )
    await _redis_memory.connect()
    logger.info("Connected to Redis at %s", settings.REDIS_URL)

    # ── Tool catalog ─────────────────────────────────────────────────
    tools: Dict[str, Any] = _scan_tools()
    logger.info("Discovered %d tools", len(tools))

    # ── Configure activities DI ──────────────────────────────────────
    activities.configure(
        nats=_nats,
        model_client=model_client,
        tools=tools,
        redis_memory=_redis_memory,
    )

    # ── Register with Restate ────────────────────────────────────────
    restate_client = RestateClient(
        admin_url=settings.RESTATE_ADMIN_URL,
    )

    return {
        "model_client": model_client,
        "restate_client": restate_client,
    }


async def _teardown() -> None:
    """Clean up connections on shutdown."""
    global _nats, _redis_memory

    if _nats is not None:
        await _nats.disconnect()
        _nats = None

    if _redis_memory is not None:
        await _redis_memory.disconnect()
        _redis_memory = None

    logger.info("Worker teardown complete")


def _scan_tools() -> Dict[str, Any]:
    """Discover and instantiate all available tools.

    Uses the catalog scanner if available, otherwise returns a minimal
    set of built-in tools.
    """
    tools: Dict[str, Any] = {}

    try:
        from raavan.catalog._scanner import scan_tools

        discovered = scan_tools()
        for tool in discovered:
            tools[tool.name] = tool
    except Exception as exc:
        logger.warning("Tool scan failed, using empty catalog: %s", exc)

    return tools


async def _register_with_restate(
    restate_client: Any,
    worker_url: str,
) -> None:
    """Register this worker's Restate deployment."""
    try:
        await restate_client.register_deployment(worker_url)
        logger.info("Registered with Restate admin")
    except Exception as exc:
        logger.warning(
            "Failed to register with Restate (will retry on first request): %s",
            exc,
        )


def main() -> None:
    """Entry-point: ``python -m raavan.distributed.worker``."""
    import argparse

    from raavan.shared.observability.logger import setup_logging

    parser = argparse.ArgumentParser(description="Restate agent worker")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    parser.add_argument("--port", type=int, default=9080, help="Bind port")
    args = parser.parse_args()

    setup_logging()

    worker_url = f"http://{args.host}:{args.port}"

    async def _start() -> None:
        deps = await _setup()
        await _register_with_restate(deps["restate_client"], worker_url)

    # Setup before uvicorn starts
    asyncio.run(_start())

    # Graceful shutdown on SIGTERM
    def _handle_sigterm(signum: int, frame: Any) -> None:
        logger.info("Received SIGTERM, shutting down")
        asyncio.run(_teardown())
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _handle_sigterm)

    # Serve the Restate ASGI app
    uvicorn.run(
        "raavan.distributed.restate_app:app",
        host=args.host,
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
