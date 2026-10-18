"""Tool Executor — FastAPI application.

Entry point: uvicorn substrate_cloud.services.tool_executor.app:app --port 8015
"""

from __future__ import annotations

import logging

import os
from contextlib import asynccontextmanager

from substrate_cloud.services.base import AUTHENTICATED, create_service_app
from substrate_cloud.services.tool_executor.executor import ToolRegistry
from substrate_cloud.services.tool_executor.routes import router
from substrate_cloud.shared.service_client import ServiceClient
from substrate_cloud.shared.service_urls import service_url
from substrate_cloud.shared.settings import settings

logger = logging.getLogger(__name__)


def _load_default_tools(code_interpreter_tool=None, task_store=None) -> list:
    """Load all available tools for the registry."""
    tools = []

    try:
        from substrate.integrations.tools.web.surfer import WebSurferTool

        tools.append(WebSurferTool())
    except Exception:
        logger.debug("WebSurferTool not available")

    try:
        from substrate.integrations.tools.task_manager.tool import TaskManagerTool
        from substrate.stores import Store

        tools.append(TaskManagerTool(store=task_store or Store.at().tasks))
    except Exception:
        logger.debug("TaskManagerTool not available")

    if code_interpreter_tool is not None:
        tools.append(code_interpreter_tool)

    return tools


@asynccontextmanager
async def lifespan(app):
    from substrate_cloud.factory import init_file_access, open_store

    store = open_store(settings)
    app.state.file_access, _ = init_file_access(settings, store)
    app.state.conversation_client = ServiceClient(
        service_url("conversation"), "conversation"
    )

    # Code interpreter — explicit, fail-closed runtime selection (same switch as
    # the monolith's substrate_cloud/factory.py wiring). Defaults to k8s
    # here since this service only runs in cluster deployments.
    code_interpreter_tool = None
    try:
        from substrate.integrations.tools.code_interpreter import CodeInterpreterTool
        from substrate.integrations.tools.code_interpreter.code_interpreter.runtimes.factory import (
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

    # Tool Registry
    task_store = store.tasks
    registry = ToolRegistry()
    registry.register_many(
        _load_default_tools(
            code_interpreter_tool=code_interpreter_tool, task_store=task_store
        )
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
    await app.state.file_access.aclose()
    await app.state.conversation_client.aclose()
    await store.aclose()


app = create_service_app(
    title="Tool Executor",
    lifespan=lifespan,
)
app.include_router(router, prefix="/api/v1", dependencies=AUTHENTICATED)
