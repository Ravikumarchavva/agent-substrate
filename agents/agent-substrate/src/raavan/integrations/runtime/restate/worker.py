"""Restate worker entry-point — ``python -m raavan.integrations.runtime.restate.worker``.

Lifecycle:
1. **Setup** — create model client, optional NATS bridge, Redis memory,
   scan tools, create catalog + chain runtime, call ``activities.configure()``.
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

_streaming: Any = None
_redis_memory: Any = None


async def _setup() -> Dict[str, Any]:
    """Initialise all dependencies and configure activities."""
    global _streaming, _redis_memory

    from raavan.integrations.runtime.restate import activities
    from raavan.integrations.runtime.restate.client import RestateWorkflowClient
    from raavan.integrations.llm.openai.openai_client import OpenAIClient
    from raavan.integrations.memory.redis_memory import RedisMemory

    model_client = OpenAIClient(api_key=settings.OPENAI_API_KEY)

    # Optional NATS streaming bridge
    try:
        from raavan.integrations.runtime.nats.bridge import NATSBridge

        _streaming = NATSBridge(nats_url=settings.NATS_URL)
        await _streaming.connect()
        logger.info("Connected to NATS at %s", settings.NATS_URL)
    except (ImportError, Exception) as exc:
        logger.info(
            "NATS streaming unavailable (%s) — running without SSE fan-out", exc
        )
        _streaming = None

    # Redis memory pool
    _redis_memory = RedisMemory(
        session_id="worker-pool",
        redis_url=settings.REDIS_URL,
    )
    await _redis_memory.connect()
    logger.info("Connected to Redis at %s", settings.REDIS_URL)

    # Tool catalog
    tools: Dict[str, Any] = _scan_tools()
    logger.info("Discovered %d tools", len(tools))

    # Catalog + chain runtime (for pipeline/chain workflows)
    catalog = None
    data_store = None
    chain_runtime = None
    try:
        from raavan.catalog._chain_runtime import ChainRuntime
        from raavan.catalog._data_ref import DataRefStore

        data_store = DataRefStore(redis_url=settings.REDIS_URL)
        await data_store.connect()

        from raavan.core.tools.catalog import ToolRegistry

        catalog = ToolRegistry()
        for tool in tools.values():
            catalog.register_tool(tool)

        chain_runtime = ChainRuntime(catalog=catalog, data_store=data_store)
    except Exception as exc:
        logger.warning("Pipeline/chain support unavailable: %s", exc)

    # Configure activities DI
    activities.configure(
        streaming=_streaming,
        model_client=model_client,
        tools=tools,
        redis_memory=_redis_memory,
        catalog=catalog,
        data_store=data_store,
        chain_runtime=chain_runtime,
    )

    # Restate client for admin registration
    restate_client = RestateWorkflowClient(
        admin_url=getattr(settings, "RESTATE_ADMIN_URL", "http://localhost:9070"),
    )
    await restate_client.connect()

    return {"model_client": model_client, "restate_client": restate_client}


async def _teardown() -> None:
    """Clean up connections on shutdown."""
    global _streaming, _redis_memory

    if _streaming is not None:
        await _streaming.disconnect()
        _streaming = None

    if _redis_memory is not None:
        await _redis_memory.disconnect()
        _redis_memory = None

    logger.info("Worker teardown complete")


def _scan_tools() -> Dict[str, Any]:
    """Discover and instantiate all available tools."""
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
    """Entry-point: ``python -m raavan.integrations.runtime.restate.worker``."""
    import argparse

    from raavan.logger import setup_logging

    parser = argparse.ArgumentParser(description="Restate agent worker")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    parser.add_argument("--port", type=int, default=9080, help="Bind port")
    args = parser.parse_args()

    setup_logging()

    worker_url = f"http://{args.host}:{args.port}"

    async def _start() -> None:
        deps = await _setup()
        await _register_with_restate(deps["restate_client"], worker_url)

    asyncio.run(_start())

    def _handle_sigterm(signum: int, frame: Any) -> None:
        logger.info("Received SIGTERM, shutting down")
        asyncio.run(_teardown())
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _handle_sigterm)

    uvicorn.run(
        "raavan.integrations.runtime.restate.app:app",
        host=args.host,
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
