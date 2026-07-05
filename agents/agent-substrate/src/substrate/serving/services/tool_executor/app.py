"""Tool Executor — FastAPI application.

Entry point: uvicorn substrate.serving.services.tool_executor.app:app --port 8015
"""

from __future__ import annotations
from substrate.logger import setup_logging

import os
from contextlib import asynccontextmanager

from substrate.infrastructure.cache.redis import RedisConnector
from substrate.serving.services.base import create_service_app
from substrate.serving.services.tool_executor.executor import ToolRegistry
from substrate.serving.services.tool_executor.routes import router
from substrate.serving.shared.events.factory import get_event_bus

logger = setup_logging()


def _load_default_tools(ci_http_client=None) -> list:
    """Load all available tools for the registry."""
    tools = []

    try:
        from substrate.capabilities.tools.web.surfer import WebSurferTool

        tools.append(WebSurferTool())
    except Exception:
        logger.debug("WebSurferTool not available")

    try:
        from substrate.capabilities.tools.task_manager.tool import TaskManagerTool
        from substrate.agents.storage.tasks import GlobalTaskStore

        tools.append(TaskManagerTool(store=GlobalTaskStore.get()))
    except Exception:
        logger.debug("TaskManagerTool not available")

    if ci_http_client is not None:
        try:
            from substrate.capabilities.tools.code_interpreter.tool import (
                CodeInterpreterTool,
            )

            tools.append(CodeInterpreterTool(http_client=ci_http_client))
            logger.info("CodeInterpreterTool registered (HTTP mode)")
        except Exception:
            logger.debug("CodeInterpreterTool not available")

    try:
        # FileManagerTool requires file_store + session_factory from server context.
        # In microservices the Artifact service owns storage; skip for now — agent
        # uses the Artifact service directly via API.
        pass
    except Exception:
        pass

    return tools


@asynccontextmanager
async def lifespan(app):
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    ci_url = os.environ.get("CODE_INTERPRETER_URL", "")
    artifact_url = os.environ.get("ARTIFACT_SERVICE_URL", "http://localhost:8018")

    # Redis + EventBus
    redis_connector = RedisConnector(redis_url)
    await redis_connector.connect()
    app.state.redis = redis_connector.client

    event_bus = get_event_bus(redis_url)
    await event_bus.connect()
    app.state.event_bus = event_bus

    # Code Interpreter HTTP client (optional — only if URL is configured)
    ci_client = None
    if ci_url:
        try:
            from substrate.capabilities.tools.code_interpreter.http_client import (
                CodeInterpreterClient,
            )

            replicas = int(os.environ.get("CI_REPLICAS", "1"))
            headless = os.environ.get("CI_HEADLESS_SERVICE", "")
            namespace = os.environ.get("CI_NAMESPACE", "af-runtime")
            ci_client = CodeInterpreterClient(
                base_url=ci_url,
                replicas=replicas,
                headless_service=headless,
                namespace=namespace,
            )
            logger.info(
                "CodeInterpreterClient configured: url=%s replicas=%d",
                ci_url,
                replicas,
            )
        except Exception as exc:
            logger.warning("CodeInterpreterClient init failed: %s", exc)

    app.state.ci_client = ci_client
    app.state.artifact_url = artifact_url.rstrip("/")

    # Tool Registry
    registry = ToolRegistry()
    registry.register_many(_load_default_tools(ci_http_client=ci_client))
    app.state.tool_registry = registry

    logger.info(
        "Tool Executor started — %d tools registered, CI=%s",
        registry.tool_count,
        "enabled" if ci_client else "disabled",
    )
    yield

    if ci_client:
        await ci_client.close()
    await app.state.event_bus.disconnect()
    await redis_connector.disconnect()


app = create_service_app(
    title="Tool Executor",
    lifespan=lifespan,
)
app.include_router(router)
