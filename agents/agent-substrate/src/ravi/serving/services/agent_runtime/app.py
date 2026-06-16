"""Agent Runtime — FastAPI application.

Entry point: uvicorn ravi.serving.services.agent_runtime.app:app --port 8014
"""

from __future__ import annotations
from ravi.logger import setup_logging

import os
from contextlib import asynccontextmanager

import redis.asyncio as aioredis

from ravi.agents.runtime.runtime import Runtime
from ravi.capabilities.history.redis_history import RedisHistoryProvider
from ravi.integrations.llm.openai.openai_client import OpenAIClient
from ravi.serving.services.agent_runtime.routes import router
from ravi.serving.services.base import create_service_app
from ravi.serving.shared.events.factory import get_event_bus

logger = setup_logging()


def _load_tools():
    """Load the default tool set for the agent runtime."""
    tools = []

    try:
        from ravi.capabilities.tools.web.surfer import WebSurferTool

        tools.append(WebSurferTool())
    except Exception:
        logger.debug("WebSurferTool not available")

    return tools


@asynccontextmanager
async def _runtime_cm(backend: str, pg_url: str, redis_url: str):
    """Yield a durable Postgres-backed runtime, or in-memory as opt-out.

    Mirrors the monolith's RUNTIME_BACKEND selection so both deployment modes
    are durable by default.
    """
    if backend == "postgres" and pg_url:
        from ravi.infrastructure.runtime import build_postgres_runtime

        async with build_postgres_runtime(
            postgres_url=pg_url, redis_url=redis_url
        ) as rt:
            logger.info("Agent Runtime: durable (Postgres EventLog + Redis journal)")
            yield rt
    else:
        async with Runtime() as rt:
            logger.info("Agent Runtime: in-memory (no durability)")
            yield rt


@asynccontextmanager
async def lifespan(app):
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    conversation_url = os.environ.get(
        "CONVERSATION_SERVICE_URL",
        "http://localhost:8012",
    )
    backend = os.environ.get("RUNTIME_BACKEND", "postgres").lower()
    pg_url = (
        os.environ.get("DATABASE_URL", "")
        or os.environ.get("ASYNC_DATABASE_URL", "")
    ).replace("+asyncpg", "")

    async with _runtime_cm(backend, pg_url, redis_url) as runtime:
        app.state.runtime = runtime

        # Redis
        app.state.redis = aioredis.from_url(redis_url, decode_responses=True)

        event_bus = get_event_bus(redis_url)
        await event_bus.connect()
        app.state.event_bus = event_bus

        # Redis history (shared, multi-session)
        history = RedisHistoryProvider(redis_url=redis_url)
        await history.connect()
        app.state.history = history

        # Model client
        app.state.model_client = OpenAIClient(
            model=os.environ.get("MODEL_NAME", "gpt-4o"),
        )

        # Tools
        app.state.tools = _load_tools()

        # System instructions
        app.state.system_instructions = os.environ.get(
            "SYSTEM_INSTRUCTIONS",
            "You are a helpful assistant.",
        )

        # Service URLs
        app.state.conversation_service_url = conversation_url

        logger.info("Agent Runtime started — %d tools loaded", len(app.state.tools))
        yield

        await history.disconnect()
        await app.state.event_bus.disconnect()
        await app.state.redis.aclose()


app = create_service_app(
    title="Agent Runtime",
    lifespan=lifespan,
)
app.include_router(router)
