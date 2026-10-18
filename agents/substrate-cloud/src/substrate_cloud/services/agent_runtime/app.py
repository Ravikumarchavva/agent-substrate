"""Agent Runtime: FastAPI application.

Entry point: uvicorn substrate_cloud.services.agent_runtime.app:app --port 8014
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from substrate.runtime import Runtime

from substrate_cloud.factory import (
    build_runtime_default_tools,
    init_llm_clients,
    open_store,
)
from substrate_cloud.services.agent_runtime.routes import router
from substrate_cloud.services.agent_runtime.service import cancel_requested
from substrate_cloud.services.base import AUTHENTICATED, create_service_app
from substrate_cloud.shared.events import types as events
from substrate_cloud.shared.events.factory import get_event_bus
from substrate_cloud.shared.settings import settings

logger = logging.getLogger(__name__)

SYSTEM_INSTRUCTIONS = (
    "You are an intelligent general-purpose AI assistant. "
    "You reason carefully, use tools purposefully, and communicate with clarity and precision."
)


async def consume_cancellations(app) -> None:
    """Stop the runs the job controller asks to cancel."""
    async for envelope in app.state.event_bus.subscribe(
        events.CANCEL_REQUESTED, group="agent-runtime-cancel"
    ):
        try:
            await cancel_requested(app.state.runtime, envelope)
        except Exception:
            logger.exception("Failed to cancel for %s", envelope.event_id)


@asynccontextmanager
async def lifespan(app):
    store = open_store(settings)
    async with Runtime(store) as runtime:
        app.state.runtime = runtime
        app.state.history = store.threads
        app.state.model_client = init_llm_clients(settings).model_client
        app.state.tools = build_runtime_default_tools()
        app.state.system_instructions = SYSTEM_INSTRUCTIONS
        app.state.outcome_tasks = set()

        event_bus = get_event_bus(settings.REDIS_URL)
        await event_bus.connect()
        app.state.event_bus = event_bus
        cancels = asyncio.create_task(
            consume_cancellations(app), name="cancel-listener"
        )

        logger.info("Agent Runtime started — %d tools loaded", len(app.state.tools))
        yield

        cancels.cancel()
        for task in list(app.state.outcome_tasks):
            task.cancel()
        await asyncio.gather(cancels, *app.state.outcome_tasks, return_exceptions=True)
        await event_bus.disconnect()
    await store.aclose()


app = create_service_app(title="Agent Runtime", lifespan=lifespan)
app.include_router(router, prefix="/api/v1", dependencies=AUTHENTICATED)
