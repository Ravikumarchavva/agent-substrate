"""Agent Runtime — FastAPI application.

Entry point: uvicorn substrate.serving.services.agent_runtime.app:app --port 8014
"""

from __future__ import annotations

from substrate.logger import setup_logging

import asyncio
import os
from contextlib import asynccontextmanager

import redis.asyncio as aioredis

from substrate.infrastructure.serving_factory import (
    build_history_provider,
    build_runtime_default_tools,
)
from substrate.integrations.llm.factory import create_model_client
from substrate.serving.services.agent_runtime.routes import router
from substrate.serving.services.base import create_service_app
from substrate.serving.shared.events.factory import get_event_bus

logger = setup_logging()


@asynccontextmanager
async def _runtime_cm(backend: str, pg_url: str):
    if backend == "postgres" and pg_url:
        from substrate.infrastructure.runtime import build_postgres_runtime

        async with build_postgres_runtime(postgres_url=pg_url) as rt:
            logger.info("Agent Runtime: durable (Postgres EventLog)")
            yield rt
    else:
        from substrate.agents.runtime import Runtime

        async with Runtime() as rt:
            logger.info("Agent Runtime: in-memory (no durability)")
            yield rt


async def _cancel_listener(runtime: object, event_bus: object) -> None:
    """Consume job.cancel_requested events and cancel the local runtime run."""
    try:
        async for envelope in event_bus.subscribe(  # type: ignore[union-attr]
            "job.cancel_requested",
            group="agent-runtime-cancel",
        ):
            run_id: str = envelope.payload.get("run_id", "")
            if run_id:
                logger.info("Cancelling run %s via event bus", run_id)
                try:
                    await runtime.cancel(run_id)  # type: ignore[union-attr]
                except Exception:
                    logger.exception("Failed to cancel run %s", run_id)
    except asyncio.CancelledError:
        pass


@asynccontextmanager
async def lifespan(app):
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    conversation_url = os.environ.get(
        "CONVERSATION_SERVICE_URL", "http://localhost:8012"
    )
    backend = os.environ.get("RUNTIME_BACKEND", "postgres").lower()
    pg_url = (
        os.environ.get("DATABASE_URL", "") or os.environ.get("ASYNC_DATABASE_URL", "")
    ).replace("+asyncpg", "")

    async with _runtime_cm(backend, pg_url) as runtime:
        app.state.runtime = runtime

        app.state.redis = aioredis.from_url(redis_url, decode_responses=True)

        event_bus = get_event_bus(redis_url)
        await event_bus.connect()
        app.state.event_bus = event_bus

        history = await build_history_provider(redis_url)
        app.state.history = history

        app.state.model_client = create_model_client(
            os.environ.get("MODEL_NAME", "gpt-4o"),
            api_keys={"openai": os.environ.get("OPENAI_API_KEY", "")},
        )

        app.state.tools = build_runtime_default_tools()
        app.state.system_instructions = os.environ.get(
            "SYSTEM_INSTRUCTIONS",
            "You are Ravi, an intelligent general-purpose AI assistant. "
            "You reason carefully, use tools purposefully, and communicate with clarity and precision.",
        )
        app.state.conversation_service_url = conversation_url
        app.state.forwarding_tasks: dict[str, asyncio.Task] = {}

        cancel_task = asyncio.create_task(
            _cancel_listener(runtime, event_bus), name="cancel-listener"
        )

        logger.info("Agent Runtime started — %d tools loaded", len(app.state.tools))
        yield

        cancel_task.cancel()
        try:
            await cancel_task
        except asyncio.CancelledError:
            pass

        for task in list(app.state.forwarding_tasks.values()):
            task.cancel()

        await history.disconnect()
        await app.state.event_bus.disconnect()
        await app.state.redis.aclose()


app = create_service_app(
    title="Agent Runtime",
    lifespan=lifespan,
)
app.include_router(router)
