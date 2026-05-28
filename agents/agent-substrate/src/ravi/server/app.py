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
from ravi.logger import setup_logging

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from ravi.configs.settings import settings
from ravi.shared.observability.telemetry import (
    configure_opentelemetry,
    shutdown_opentelemetry,
)
from ravi.server.database import close_db, init_db
from ravi.server.dependencies import ServerDependencies
from ravi.server.routes.admin import router as admin_router
from ravi.server.routes.auth import router as auth_router
from ravi.server.routes.cancel import router as cancel_router
from ravi.server.routes.chat import router as chat_router
from ravi.server.routes.code_interpreter import (
    router as code_interpreter_router,
)
from ravi.server.routes.elements import router as elements_router
from ravi.server.routes.feedback import router as feedback_router
from ravi.server.routes.audio import router as audio_router
from ravi.server.routes.files import router as files_router
from ravi.server.routes.hitl import router as hitl_router
from ravi.server.routes.mcp_apps import router as mcp_apps_router
from ravi.server.routes.spotify_oauth import router as spotify_oauth_router
from ravi.server.routes.workspace_oauth import router as workspace_oauth_router
from ravi.server.routes.pipelines import router as pipelines_router
from ravi.server.routes.tasks import router as tasks_router
from ravi.server.routes.threads import router as threads_router
from ravi.server.routes.triggers import router as triggers_router
from ravi.server.routes.workflows import router as workflows_router
from ravi.server.routes.rag import router as rag_router
from ravi.server.routes.replay import router as replay_router
from ravi.server._lifespan import (
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
    app.state.redis_memory = infra.redis_memory
    app.state.redis_client = infra.redis_client
    app.state.runtime = infra.runtime
    app.state.file_store = infra.file_store
    app.state.session_factory = infra.session_factory
    app.state.vector_store = infra.vector_store
    app.state.rag_pipeline = infra.rag_pipeline
    app.state.data_store = infra.data_store
    app.state.bridge_registry = infra.bridge_registry

    # JWT secret for shared auth middleware
    app.state.jwt_secret = settings.JWT_SECRET

    # Tool registry
    tools = await init_tool_registry(
        settings,
        file_store=infra.file_store,
        session_factory=infra.session_factory,
        bridge_registry=infra.bridge_registry,
        redis_client=infra.redis_client,
    )
    app.state.tools = tools.catalog
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

    # S14: replay gate — operator-controlled admission for envelope replays.
    from ravi.platform.observability._in_memory import InMemoryReplayGate
    app.state.replay_gate = InMemoryReplayGate()

    # MCP server registry: maps server_id → RegistryMcpServer dict.
    # Populated at runtime via POST /builder/mcp-servers (in-memory, not persisted).
    app.state.mcp_servers = {}  # dict[str, dict]

    # Runtime services (chains, pipelines, workflows, triggers)
    rt = await init_runtime_services(
        settings,
        catalog=tools.catalog,
        data_store=infra.data_store,
        session_factory=infra.session_factory,
        runtime=infra.runtime,
        tools_requiring_approval=tools.tools_requiring_approval,
        tool_timeout=app.state.tool_timeout,
    )
    app.state.chain_runtime = rt.chain_runtime
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
        redis_memory=app.state.redis_memory,
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
    if getattr(app.state, "runtime", None):
        await app.state.runtime.stop()
    if getattr(app.state, "workflow_client", None):
        await app.state.workflow_client.disconnect()
    if getattr(app.state, "trigger_scheduler", None):
        await app.state.trigger_scheduler.stop()
    if getattr(app.state, "condition_monitor", None):
        await app.state.condition_monitor.stop()
    if getattr(app.state, "data_store", None):
        await app.state.data_store.disconnect()
    if getattr(app.state, "file_store", None):
        await app.state.file_store.shutdown()
    if getattr(app.state, "ci_client", None):
        await app.state.ci_client.close()  # type: ignore[union-attr]
    if getattr(app.state, "redis_memory", None):
        await app.state.redis_memory.disconnect()
    if getattr(app.state, "redis_client", None):
        await app.state.redis_client.aclose()
    for tool in app.state.tools.all_tools():
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
    app.include_router(elements_router)
    app.include_router(feedback_router)
    app.include_router(audio_router)
    app.include_router(files_router)
    app.include_router(mcp_apps_router)
    app.include_router(spotify_oauth_router)
    app.include_router(workspace_oauth_router)
    app.include_router(tasks_router)
    app.include_router(pipelines_router)
    app.include_router(workflows_router)
    app.include_router(triggers_router)
    app.include_router(rag_router)
    app.include_router(replay_router)

    # Visual Builder — only mounted when ENABLE_BUILDER=true (zero prod footprint)
    if settings.ENABLE_BUILDER:
        from ravi.server.routes.builder import router as builder_router

        app.include_router(builder_router)
        setup_logging().info("Builder API mounted at /builder")

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
