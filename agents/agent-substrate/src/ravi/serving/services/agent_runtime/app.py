"""Agent Runtime — FastAPI application.

Entry point: uvicorn ravi.serving.services.agent_runtime.app:app --port 8014
"""

from __future__ import annotations
from ravi.logger import setup_logging

import os
from contextlib import asynccontextmanager

import redis.asyncio as aioredis

from ravi.integrations.history.redis_history import RedisHistoryProvider
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
async def lifespan(app):
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    conversation_url = os.environ.get(
        "CONVERSATION_SERVICE_URL",
        "http://localhost:8012",
    )

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
