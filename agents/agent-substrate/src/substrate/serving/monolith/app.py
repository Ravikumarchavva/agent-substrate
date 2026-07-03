"""Production FastAPI application for the chat server."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from substrate.serving.shared.settings import settings
from substrate.infrastructure.serving_factory import (
    Infrastructure,
    LLMClients,
    RuntimeServices,
    ToolboxResult,
    init_infrastructure,
    init_llm_clients,
    init_runtime_services,
    init_tool_registry,
    resume_pending_runs,
)
from substrate.serving.monolith.database import init_db
from substrate.serving.monolith.dependencies import ServerDependencies
from substrate.serving.monolith.routes.admin import router as admin_router
from substrate.serving.monolith.routes.audio import router as audio_router
from substrate.serving.monolith.routes.auth import router as auth_router
from substrate.serving.monolith.routes.cancel import router as cancel_router
from substrate.serving.monolith.routes.chat import router as chat_router
from substrate.serving.monolith.routes.code_interpreter import (
    router as code_interpreter_router,
)
from substrate.serving.monolith.routes.feedback import router as feedback_router
from substrate.serving.monolith.routes.files import router as files_router
from substrate.serving.monolith.routes.hitl import router as hitl_router
from substrate.serving.monolith.routes.mcp_apps import router as mcp_apps_router
from substrate.serving.monolith.routes.pipelines import router as pipelines_router
from substrate.serving.monolith.routes.rag import router as rag_router
from substrate.serving.monolith.routes.rate_limit import router as rate_limit_router
from substrate.serving.monolith.routes.connector_tokens import (
    router as connector_tokens_router,
)
from substrate.serving.monolith.routes.tasks import router as tasks_router
from substrate.serving.monolith.routes.threads import router as threads_router
from substrate.serving.monolith.routes.triggers import router as triggers_router
from substrate.serving.monolith.routes.scheduled import router as scheduled_router
from substrate.serving.shared.observability.telemetry import (
    configure_opentelemetry,
    shutdown_opentelemetry,
)
from substrate.serving.shared.rate_limit import rate_limit_settings
from substrate.logger import setup_logging

logger = setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""

    # Observability
    configure_opentelemetry(
        service_name="agent-framework",
        otlp_trace_endpoint=settings.OTLP_ENDPOINT,
    )

    # Database — returns (engine, session_factory), no module globals
    engine, session_factory = await init_db(settings.DATABASE_URL, echo=False)
    app.state.engine = engine
    app.state.session_factory = session_factory

    # LLM clients
    llm: LLMClients = init_llm_clients(settings)
    app.state.model_client = llm.model_client
    app.state.model_client_kwargs = llm.model_client_kwargs
    app.state.chat_model = llm.chat_model
    app.state.api_keys = llm.api_keys
    app.state.embedding_client = llm.embedding_client

    # Infrastructure (Redis, runtime, file store, vector store, RAG, data store)
    infra: Infrastructure = await init_infrastructure(
        settings,
        llm.embedding_client,
        engine=engine,
        session_factory=session_factory,
    )
    app.state.history = infra.history
    app.state.redis_client = infra.redis_client
    app.state.runtime = infra.runtime
    app.state.runtime_stack = infra.runtime_stack
    app.state.vector_store = infra.vector_store
    app.state.rag_pipeline = infra.rag_pipeline
    app.state.data_store = infra.data_store
    app.state.bridge_registry = infra.bridge_registry
    app.state.skill_manager = infra.skill_manager
    app.state.file_store = infra.file_store

    app.state.jwt_secret = settings.JWT_SECRET

    # Tool registry
    tools: ToolboxResult = await init_tool_registry(
        settings,
        session_factory=session_factory,
        bridge_registry=infra.bridge_registry,
        redis_client=infra.redis_client,
    )
    app.state.tools = tools.registry
    app.state.task_tool = tools.task_tool
    app.state.ci_client = tools.ci_client
    app.state.tools_requiring_approval = tools.tools_requiring_approval
    app.state.tool_timeout = 300.0

    # Cold resume — rebuild agents for runs orphaned by a previous process crash
    resumed = await resume_pending_runs(
        infra.runtime,
        registry=tools.registry,
        model_client=llm.model_client,
    )
    if resumed:
        logger.info("Cold resume: registered %d agent(s) for pending runs", resumed)

    _prompt_path = (
        __import__("pathlib").Path(__file__).parent / "prompts" / "default_system.md"
    )
    app.state.system_instructions = _prompt_path.read_text(encoding="utf-8").strip()

    app.state.mcp_servers = {}

    # Rate limiting — Redis sliding window, two-tier (authed by user_id, anon by IP)
    # The redis client used for rate limiting is the shared infra.redis_client.
    # `app.state.redis` is the alias rate_limit.py looks for.
    app.state.redis = infra.redis_client
    app.state.rate_limit_settings = rate_limit_settings(
        enabled=settings.RATE_LIMIT_ENABLED,
        authed_rpm=settings.RATE_LIMIT_AUTHED_RPM,
        anon_rpm=settings.RATE_LIMIT_ANON_RPM,
        window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS,
        fail_open=settings.RATE_LIMIT_FAIL_OPEN,
    )

    # Runtime services (chains, pipelines, workflows, triggers)
    rt: RuntimeServices = await init_runtime_services(
        settings,
        registry=tools.registry,
        data_store=infra.data_store,
        session_factory=session_factory,
        runtime=infra.runtime,
        tools_requiring_approval=tools.tools_requiring_approval,
        tool_timeout=app.state.tool_timeout,
        code_interpreter_tool=tools.code_interpreter_tool,
    )
    app.state.chain_bridge_registry = rt.chain_bridge_registry
    app.state.pipeline_engine = rt.pipeline_engine
    app.state.pipeline_store = rt.pipeline_store
    app.state.trigger_scheduler = rt.trigger_scheduler
    app.state.webhook_registry = rt.webhook_registry
    app.state.condition_monitor = rt.condition_monitor

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
        mcp_servers=app.state.mcp_servers,
        session_factory=app.state.session_factory,
        ci_client=app.state.ci_client,
        file_store=app.state.file_store,
        trigger_scheduler=app.state.trigger_scheduler,
    )

    for name in ("httpx", "urllib3", "openai"):
        setup_logging().setLevel(logging.WARNING)

    # ── Load persistent scheduled tasks ──────────────────────────────────────
    from substrate.serving.monolith.services.scheduled_service import (
        execute_scheduled_task,
        load_active_tasks_into_scheduler,
    )

    app.state.trigger_scheduler.set_scheduled_task_executor(
        lambda task_id: execute_scheduled_task(
            task_id,
            session_factory=app.state.session_factory,
            app_state=app.state.ctx,
        )
    )
    await load_active_tasks_into_scheduler(
        app.state.trigger_scheduler,
        app.state.session_factory,
    )

    yield

    # Shutdown
    if getattr(app.state, "runtime_stack", None):
        await app.state.runtime_stack.aclose()
    elif getattr(app.state, "runtime", None):
        await app.state.runtime.stop()
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
    await app.state.engine.dispose()
    shutdown_opentelemetry()


def create_app() -> FastAPI:
    """Build and return the FastAPI application."""
    app = FastAPI(
        title="Agent Framework Chat Server",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

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
    app.include_router(connector_tokens_router)
    app.include_router(tasks_router)
    app.include_router(pipelines_router)
    app.include_router(triggers_router)
    app.include_router(scheduled_router)
    app.include_router(rag_router)
    app.include_router(files_router)
    app.include_router(rate_limit_router)

    @app.get("/health", tags=["infra"])
    async def health():
        return {"status": "ok"}

    FastAPIInstrumentor.instrument_app(app)

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
