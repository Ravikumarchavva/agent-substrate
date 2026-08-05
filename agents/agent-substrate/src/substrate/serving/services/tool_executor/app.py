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


def _load_default_tools(code_interpreter_tool=None) -> list:
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

    if code_interpreter_tool is not None:
        tools.append(code_interpreter_tool)

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
    artifact_url = os.environ.get("ARTIFACT_SERVICE_URL", "http://localhost:8018")

    # Redis + EventBus
    redis_connector = RedisConnector(redis_url)
    await redis_connector.connect()
    app.state.redis = redis_connector.client

    event_bus = get_event_bus(redis_url)
    await event_bus.connect()
    app.state.event_bus = event_bus

    # Code interpreter — explicit, fail-closed runtime selection (same switch as
    # the monolith's infrastructure/serving_factory.py wiring). Defaults to k8s
    # here since this service only runs in cluster deployments.
    code_interpreter_tool = None
    try:
        from substrate.capabilities.tools.code_interpreter import CodeInterpreterTool
        from substrate.capabilities.tools.code_interpreter.code_interpreter.runtimes.factory import (
            build_runtime,
            network_policy,
        )

        code_interpreter_tool = CodeInterpreterTool(
            build_runtime(
                os.environ.get("SANDBOX_RUNTIME", "k8s"),
                workspace_root=os.environ.get("FILE_STORE_ROOT", "./data/workspaces"),
                runtime_class_name=os.environ.get("SANDBOX_RUNTIME_CLASS", ""),
                workspace_pvc_claim=os.environ.get("CI_WORKSPACE_PVC_CLAIM") or None,
                python_bin=os.environ.get("SANDBOX_PYTHON", ""),
            ),
            network=network_policy(os.environ.get("SANDBOX_NETWORK_POLICY", "deny")),
        )
        logger.info(
            "Code interpreter registered (runtime=%s)",
            os.environ.get("SANDBOX_RUNTIME", "k8s"),
        )
    except Exception as exc:  # noqa: BLE001 - degrade to "no CI", never "no isolation"
        logger.warning("Code interpreter disabled: sandbox unavailable (%s)", exc)

    app.state.ci_client = code_interpreter_tool
    app.state.artifact_url = artifact_url.rstrip("/")

    # Tool Registry
    registry = ToolRegistry()
    registry.register_many(
        _load_default_tools(code_interpreter_tool=code_interpreter_tool)
    )
    app.state.tool_registry = registry

    logger.info(
        "Tool Executor started — %d tools registered, CI=%s",
        registry.tool_count,
        "enabled" if code_interpreter_tool else "disabled",
    )
    yield

    close = getattr(code_interpreter_tool, "close", None)
    if close is not None:
        await close()
    await app.state.event_bus.disconnect()
    await redis_connector.disconnect()


app = create_service_app(
    title="Tool Executor",
    lifespan=lifespan,
)
app.include_router(router)
