"""Standalone gRPC runtime node — runs as a K8s pod or local process.

Each node hosts one or more agent types and exposes them via a gRPC server.
Other nodes (or the notebook) can call these agents remotely using
``GrpcRuntime`` with ``remote_peers`` pointing here.

Usage::

    # Run locally
    python -m raavan.integrations.runtime.grpc.node \
        --listen 0.0.0.0:50051 \
        --agents echo,summarizer

    # K8s pod (via Deployment command)
    command: ["python", "-m", "raavan.integrations.runtime.grpc.node",
              "--listen", "0.0.0.0:50051", "--agents", "echo,greeter"]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import platform
import signal
from typing import Any

from raavan.core.runtime._types import MessageContext
from raavan.integrations.runtime.grpc.runtime import GrpcRuntime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("raavan.runtime.node")


# ---------------------------------------------------------------------------
# Built-in demo handlers — lightweight agents for testing
# ---------------------------------------------------------------------------


async def echo_handler(ctx: MessageContext, payload: Any) -> Any:
    """Echoes back the payload with node metadata."""
    return {
        "agent": "echo",
        "node_id": str(ctx.agent_id),
        "echo": payload,
        "runtime": type(ctx.runtime).__name__,
    }


async def greeter_handler(ctx: MessageContext, payload: Any) -> Any:
    """Returns a greeting message."""
    name = payload.get("name", "stranger") if isinstance(payload, dict) else "stranger"
    return {
        "agent": "greeter",
        "node_id": str(ctx.agent_id),
        "greeting": f"Hello, {name}! Greetings from {ctx.agent_id}.",
    }


async def summarizer_handler(ctx: MessageContext, payload: Any) -> Any:
    """Summarises text (mock — truncates to 80 chars)."""
    text = (
        payload.get("text", str(payload)) if isinstance(payload, dict) else str(payload)
    )
    summary = text[:80] + ("..." if len(text) > 80 else "")
    return {
        "agent": "summarizer",
        "node_id": str(ctx.agent_id),
        "summary": summary,
        "original_length": len(text),
    }


async def translator_handler(ctx: MessageContext, payload: Any) -> Any:
    """Mock translator — reverses the text as a 'translation'."""
    text = (
        payload.get("text", str(payload)) if isinstance(payload, dict) else str(payload)
    )
    return {
        "agent": "translator",
        "node_id": str(ctx.agent_id),
        "translation": text[::-1],
        "source_lang": "en",
        "target_lang": "reverse",
    }


HANDLERS: dict[str, Any] = {
    "echo": echo_handler,
    "greeter": greeter_handler,
    "summarizer": summarizer_handler,
    "translator": translator_handler,
}


# ---------------------------------------------------------------------------
# Node lifecycle
# ---------------------------------------------------------------------------


async def run_node(listen_address: str, agent_types: list[str]) -> None:
    """Start a ``GrpcRuntime`` node and serve until terminated."""
    runtime = GrpcRuntime(listen_address=listen_address)

    registered: list[str] = []
    for agent_type in agent_types:
        handler = HANDLERS.get(agent_type)
        if handler is None:
            logger.warning("Unknown agent type %r — skipping", agent_type)
            continue
        await runtime.register(agent_type, handler)
        registered.append(agent_type)

    if not registered:
        logger.error("No valid agent types to register — exiting")
        return

    await runtime.start()
    logger.info(
        "Node ready — listening on %s, serving: %s",
        listen_address,
        registered,
    )

    # Wait for gRPC server termination (handles SIGTERM on Linux / K8s)
    stop_event = asyncio.Event()

    if platform.system() != "Windows":
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop_event.set)
        await stop_event.wait()
    else:
        # On Windows, wait_for_termination() handles Ctrl+C
        await runtime._server.wait_for_termination()

    await runtime.stop()
    logger.info("Node stopped cleanly")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Start a standalone gRPC runtime node.",
    )
    parser.add_argument(
        "--listen",
        default=os.getenv("GRPC_LISTEN", "0.0.0.0:50051"),
        help="gRPC listen address (default: 0.0.0.0:50051)",
    )
    parser.add_argument(
        "--agents",
        default=os.getenv("GRPC_AGENTS", "echo,greeter"),
        help="Comma-separated agent types to serve",
    )
    args = parser.parse_args()

    asyncio.run(run_node(args.listen, args.agents.split(",")))


if __name__ == "__main__":
    main()
