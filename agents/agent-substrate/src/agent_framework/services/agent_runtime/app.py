"""Agent Runtime — FastAPI application.

Entry point: uvicorn agent_framework.services.agent_runtime.app:app --port 8014
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import redis.asyncio as aioredis

from agent_framework.core.memory.redis_memory import RedisMemory
from agent_framework.providers.llm.openai.openai_client import OpenAIClient
from agent_framework.services.agent_runtime.routes import router
from agent_framework.services.base import create_service_app
from agent_framework.shared.events.bus import EventBus

logger = logging.getLogger(__name__)


def _load_tools():
    """Load the default tool set for the agent runtime."""
    from agent_framework.extensions.tools.web_surfer import WebSurferTool

    tools = [WebSurferTool()]

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
    app.state.event_bus = EventBus(app.state.redis)

    # Redis Memory (shared pool)
    redis_memory = RedisMemory(
        session_id="__pool__",
        redis_url=redis_url,
    )
    await redis_memory.connect()
    app.state.redis_memory = redis_memory

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

    await redis_memory.disconnect()
    await app.state.redis.aclose()


app = create_service_app(
    title="Agent Runtime",
    lifespan=lifespan,
)
app.include_router(router)
