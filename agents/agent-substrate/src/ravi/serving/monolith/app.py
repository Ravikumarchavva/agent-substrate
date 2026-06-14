"""Production FastAPI application for the chat server.

Replaces the old ``main.py`` with proper:
  - Database lifecycle (init / shutdown)
  - OpenTelemetry setup
  - Router mounting
  - CORS middleware
  - Health endpoint
  - HITL bridge (tool approval + human input via SSE)
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from ravi.logger import setup_logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from ravi.config import settings
from ravi.serving.shared.observability.telemetry import (
    configure_opentelemetry,
    shutdown_opentelemetry,
)
from ravi.serving.monolith.database import close_db, init_db
from ravi.serving.monolith.dependencies import ServerDependencies
from ravi.serving.monolith.routes.admin import router as admin_router
from ravi.serving.monolith.routes.auth import router as auth_router
from ravi.serving.monolith.routes.cancel import router as cancel_router
from ravi.serving.monolith.routes.chat import router as chat_router
from ravi.serving.monolith.routes.code_interpreter import (
    router as code_interpreter_router,
)
from ravi.serving.monolith.routes.feedback import router as feedback_router
from ravi.serving.monolith.routes.audio import router as audio_router
from ravi.serving.monolith.routes.hitl import router as hitl_router
from ravi.serving.monolith.routes.mcp_apps import router as mcp_apps_router
from ravi.serving.monolith.routes.spotify_oauth import router as spotify_oauth_router
from ravi.serving.monolith.routes.workspace_oauth import (
    router as workspace_oauth_router,
)
from ravi.serving.monolith.routes.pipelines import router as pipelines_router
from ravi.serving.monolith.routes.tasks import router as tasks_router
from ravi.serving.monolith.routes.threads import router as threads_router
from ravi.serving.monolith.routes.triggers import router as triggers_router
from ravi.serving.monolith.routes.workflows import router as workflows_router
from ravi.serving.monolith.routes.rag import router as rag_router
from ravi.serving.monolith.routes.files import router as files_router
from ravi.serving.monolith._lifespan import (
    init_infrastructure,
    init_llm_clients,
    init_runtime_services,
    init_tool_registry,
)

# ── Lifespan ─────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""

    # ---------- STARTUP ----------
    # Observability
    configure_opentelemetry(
        service_name="agent-framework",
        otlp_trace_endpoint=settings.OTLP_ENDPOINT,
    )

    # Database
    await init_db(settings.DATABASE_URL, echo=False)

    # LLM clients
    llm = init_llm_clients(settings)
    app.state.model_client = llm.model_client
    app.state.model_client_kwargs = llm.model_client_kwargs
    app.state.chat_model = llm.chat_model
    app.state.api_keys = llm.api_keys
    app.state.embedding_client = llm.embedding_client

    # Infrastructure (Redis, runtime, file store, vector store, RAG, data store)
    infra = await init_infrastructure(settings, llm.embedding_client)
    app.state.history = infra.history
    app.state.redis_client = infra.redis_client
    app.state.runtime = infra.runtime
    app.state.runtime_stack = infra.runtime_stack
    app.state.session_factory = infra.session_factory
    app.state.vector_store = infra.vector_store
    app.state.rag_pipeline = infra.rag_pipeline
    app.state.data_store = infra.data_store
    app.state.bridge_registry = infra.bridge_registry
    app.state.skill_manager = infra.skill_manager
    app.state.file_store = infra.file_store

    # JWT secret for shared auth middleware
    app.state.jwt_secret = settings.JWT_SECRET

    # Tool registry
    tools = await init_tool_registry(
        settings,
        session_factory=infra.session_factory,
        bridge_registry=infra.bridge_registry,
        redis_client=infra.redis_client,
    )
    app.state.tools = tools.registry
    app.state.task_tool = tools.task_tool
    app.state.ci_client = tools.ci_client
    app.state.tools_requiring_approval = tools.tools_requiring_approval
    app.state.tool_timeout = 300.0  # match HITL bridge timeout

    _prompt_path = (
        __import__("pathlib").Path(__file__).parent / "prompts" / "default_system.md"
    )
    app.state.system_instructions = _prompt_path.read_text(encoding="utf-8").strip()

    # Cancel registry: maps thread_id → asyncio.Event so running streams can
    # be aborted from the POST /chat/{thread_id}/cancel endpoint.
    app.state.cancel_registry = {}  # dict[str, asyncio.Event]

    # MCP server registry: maps server_id → RegistryMcpServer dict.
    # Populated at runtime via POST /builder/mcp-servers (in-memory, not persisted).
    app.state.mcp_servers = {}  # dict[str, dict]

    # Runtime services (chains, pipelines, workflows, triggers)
    rt = await init_runtime_services(
        settings,
        registry=tools.registry,
        data_store=infra.data_store,
        session_factory=infra.session_factory,
        runtime=infra.runtime,
        tools_requiring_approval=tools.tools_requiring_approval,
        tool_timeout=app.state.tool_timeout,
        code_interpreter_tool=tools.code_interpreter_tool,
    )
    app.state.chain_bridge_registry = rt.chain_bridge_registry
    app.state.pipeline_engine = rt.pipeline_engine
    app.state.pipeline_store = rt.pipeline_store
    app.state.workflow_client = rt.workflow_client
    app.state.trigger_scheduler = rt.trigger_scheduler
    app.state.webhook_registry = rt.webhook_registry
    app.state.condition_monitor = rt.condition_monitor

    # Typed context — new code should prefer app.state.ctx over individual attrs.
    app.state.ctx = ServerDependencies(
        model_client=app.state.model_client,
        model_client_kwargs=app.state.model_client_kwargs,
        history=app.state.history,
        tools=app.state.tools,
        bridge_registry=app.state.bridge_registry,
        tools_requiring_approval=app.state.tools_requiring_approval,
        system_instructions=app.state.system_instructions,
        tool_timeout=app.state.tool_timeout,
        api_keys=app.state.api_keys,
        runtime=app.state.runtime,
        cancel_registry=app.state.cancel_registry,
        mcp_servers=app.state.mcp_servers,
        session_factory=app.state.session_factory,
        ci_client=app.state.ci_client,
        file_store=app.state.file_store,
    )

    # Quiet noisy loggers
    for name in ("httpx", "urllib3", "openai"):
        setup_logging().setLevel(logging.WARNING)

    yield

    # ---------- SHUTDOWN ----------
    # Durable runtime owns a Postgres pool + Redis client via an AsyncExitStack
    # (which also stops the runtime); the in-memory runtime is stopped directly.
    if getattr(app.state, "runtime_stack", None):
        await app.state.runtime_stack.aclose()
    elif getattr(app.state, "runtime", None):
        await app.state.runtime.stop()
    if getattr(app.state, "workflow_client", None):
        await app.state.workflow_client.disconnect()
    if getattr(app.state, "trigger_scheduler", None):
        await app.state.trigger_scheduler.stop()
    if getattr(app.state, "condition_monitor", None):
        await app.state.condition_monitor.stop()
    if getattr(app.state, "data_store", None):
        await app.state.data_store.disconnect()
    if getattr(app.state, "ci_client", None):
        await app.state.ci_client.close()  # type: ignore[union-attr]
    if getattr(app.state, "history", None):
        await app.state.history.disconnect()
    if getattr(app.state, "redis_client", None):
        await app.state.redis_client.aclose()
    if getattr(app.state, "file_store", None):
        await app.state.file_store.disconnect()
    for tool in app.state.tools.all():
        if hasattr(tool, "stop"):
            try:
                await tool.stop()  # type: ignore[union-attr]
            except Exception:
                pass
    await close_db()
    shutdown_opentelemetry()


# ── App factory ──────────────────────────────────────────────────────────────


def create_app() -> FastAPI:
    """Build and return the FastAPI application."""
    app = FastAPI(
        title="Agent Framework Chat Server",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS — origins from settings; in production set CORS_ALLOWED_ORIGINS in .env
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount routers
    app.include_router(admin_router)
    app.include_router(auth_router)
    app.include_router(threads_router)
    app.include_router(chat_router)
    app.include_router(cancel_router)
    app.include_router(code_interpreter_router)
    app.include_router(hitl_router)
    app.include_router(feedback_router)
    app.include_router(audio_router)
    app.include_router(mcp_apps_router)
    app.include_router(spotify_oauth_router)
    app.include_router(workspace_oauth_router)
    app.include_router(tasks_router)
    app.include_router(pipelines_router)
    app.include_router(workflows_router)
    app.include_router(triggers_router)
    app.include_router(rag_router)
    app.include_router(files_router)

    # Health check
    @app.get("/health", tags=["infra"])
    async def health():
        return {"status": "ok"}

    # Instrument with OpenTelemetry
    FastAPIInstrumentor.instrument_app(app)

    return app


# ── Module-level app (for `uvicorn server.app:app`) ──────────────────────────

app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
