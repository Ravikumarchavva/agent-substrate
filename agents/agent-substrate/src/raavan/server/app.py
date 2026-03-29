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
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from raavan.configs.settings import settings
from raavan.core.memory.redis_memory import RedisMemory
from raavan.catalog.tools.human_input.tool import AskHumanTool
from raavan.integrations.llm.openai.openai_client import OpenAIClient
from raavan.integrations.audio.openai import OpenAIAudioClient
from raavan.shared.observability.telemetry import (
    configure_opentelemetry,
    shutdown_opentelemetry,
)
from raavan.server.database import close_db, get_session_factory, init_db
from raavan.server.context import ServerContext
from raavan.server.routes.admin import router as admin_router
from raavan.server.routes.auth import router as auth_router
from raavan.server.routes.cancel import router as cancel_router
from raavan.server.routes.chat import router as chat_router
from raavan.server.routes.code_interpreter import (
    router as code_interpreter_router,
)
from raavan.server.routes.elements import router as elements_router
from raavan.server.routes.feedback import router as feedback_router
from raavan.server.routes.audio import router as audio_router
from raavan.server.routes.files import router as files_router
from raavan.server.routes.hitl import router as hitl_router
from raavan.server.routes.mcp_apps import router as mcp_apps_router
from raavan.server.routes.spotify_oauth import router as spotify_oauth_router
from raavan.server.routes.pipelines import router as pipelines_router
from raavan.server.routes.tasks import router as tasks_router
from raavan.server.routes.threads import router as threads_router
from raavan.server.routes.triggers import router as triggers_router
from raavan.server.routes.workflows import router as workflows_router
from raavan.core.tools.base_tool import ToolRisk
from raavan.core.tools.builtin_tools import CalculatorTool, GetCurrentTimeTool
from raavan.core.tools.catalog import CapabilityRegistry
from raavan.core.storage.factory import create_file_store
from raavan.catalog.tools.code_interpreter import CodeInterpreterTool
from raavan.catalog.tools.code_interpreter.http_client import (
    CodeInterpreterClient,
)
from raavan.catalog.tools.file_manager.tool import FileManagerTool
from raavan.integrations.mcp.app_tools import (
    ColorPaletteTool,
    DataVisualizerTool,
    JsonExplorerTool,
    KanbanBoardTool,
    MarkdownPreviewerTool,
    SpotifyPlayerTool,
)
from raavan.integrations.spotify.client import SpotifyService
from raavan.server.sse.bridge import BridgeRegistry
from raavan.catalog.tools.task_manager.tool import TaskManagerTool

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

    # Redis — primary session store for stateless agents
    redis_memory = RedisMemory(
        redis_url=settings.REDIS_URL,
        default_ttl=settings.REDIS_SESSION_TTL,
        max_messages=settings.SESSION_MAX_MESSAGES,
    )
    await redis_memory.connect()
    app.state.redis_memory = redis_memory

    # JWT secret for shared auth middleware
    app.state.jwt_secret = settings.JWT_SECRET

    # Shared agent dependencies (injected into routes via app.state)
    app.state.model_client = OpenAIClient(
        model="gpt-4o-mini",
        api_key=settings.OPENAI_API_KEY,
    )

    # Provider-agnostic audio client (transcription, TTS, realtime)
    app.state.audio_client = OpenAIAudioClient(
        api_key=settings.OPENAI_API_KEY,
    )

    # HITL bridge registry: one WebHITLBridge per active thread (conversation).
    # Bridges are created lazily on SSE stream start and destroyed on close.
    bridge_registry = BridgeRegistry(response_timeout=300.0)
    app.state.bridge_registry = bridge_registry

    # --- keep app.state.bridge as a sentinel-less stub for task SSE events ---
    # TaskManagerTool emits via a dynamic closure that routes through the
    # correct per-thread bridge at call time (safe with concurrent requests).
    from raavan.catalog.tools.task_manager.tool import (
        current_thread_id as _task_thread_id,
    )

    async def _task_event_emitter(event: dict) -> None:
        """Emit task SSE events to the active bridge for the current thread."""
        tid = _task_thread_id.get("default")
        await bridge_registry.emit(tid, event)

    # AskHumanTool is created per-request in _get_agent_deps (needs thread bridge).
    # Remove global bridge from state — routes now use bridge_registry directly.

    # TaskManagerTool — emitter wired at startup via dynamic closure
    task_tool = TaskManagerTool(event_emitter=_task_event_emitter)
    app.state.task_tool = task_tool

    # AskHumanTool placeholder (a real per-thread tool is built in _get_agent_deps)
    # Include a placeholder here so the tools registry returns it.
    ask_tool = AskHumanTool(handler=None, max_requests_per_run=5)  # type: ignore[arg-type]
    spotify_svc = None
    if settings.SPOTIFY_CLIENT_ID and settings.SPOTIFY_CLIENT_SECRET:
        spotify_svc = SpotifyService(
            client_id=settings.SPOTIFY_CLIENT_ID,
            client_secret=settings.SPOTIFY_CLIENT_SECRET,
        )

    # ── Code Interpreter (HTTP client → separate pod) ────────────────────
    code_interpreter_tool: CodeInterpreterTool | None = None
    ci_client: CodeInterpreterClient | None = None

    ci_url = getattr(settings, "CODE_INTERPRETER_URL", "") or os.environ.get(
        "CODE_INTERPRETER_URL", ""
    )
    if ci_url:
        ci_client = CodeInterpreterClient(
            base_url=ci_url,
            auth_token=os.environ.get("CI_AUTH_TOKEN", ""),
            replicas=int(os.environ.get("CI_REPLICAS", "1")),
            headless_service=os.environ.get("CI_HEADLESS_SERVICE", ""),
            namespace=os.environ.get("CI_NAMESPACE", "agent-framework"),
        )
        code_interpreter_tool = CodeInterpreterTool(http_client=ci_client)
        app.state.ci_client = ci_client
        logging.getLogger(__name__).info("Code interpreter connected → %s", ci_url)
    else:
        # Fallback: try local mode (direct Firecracker, for dev)
        try:
            code_interpreter_tool = CodeInterpreterTool()  # auto-detect from env
            if code_interpreter_tool._mode != "none":
                await code_interpreter_tool.start()
                logging.getLogger(__name__).info(
                    "Code interpreter started (local mode)"
                )
            else:
                code_interpreter_tool = None
                logging.getLogger(__name__).info(
                    "Code interpreter disabled (no URL configured)"
                )
        except Exception as e:
            logging.getLogger(__name__).warning("Code interpreter disabled: %s", e)
            code_interpreter_tool = None

    app.state.ci_client = ci_client

    # ── File Store (local / S3 / encrypted) ──────────────────────────────
    file_store = create_file_store(settings)
    await file_store.startup()
    app.state.file_store = file_store

    # Session factory (needed by FileManagerTool and routes)
    session_factory = get_session_factory()
    app.state.session_factory = session_factory

    file_manager_tool = FileManagerTool(
        file_store=file_store,
        session_factory=session_factory,
    )

    catalog = CapabilityRegistry()
    catalog.register_tool(
        ask_tool,
        category="communication",
        tags=["human", "input", "question", "approval", "hitl"],
        aliases=["human_input"],
    )
    catalog.register_tool(
        task_tool,
        category="development/project",
        tags=["task", "kanban", "todo", "project", "plan", "track"],
        aliases=["task_manager"],
    )
    catalog.register_tool(
        file_manager_tool,
        category="data/management",
        tags=["file", "upload", "download", "read", "write", "storage"],
        aliases=["file_tool"],
    )
    catalog.register_tool(
        CalculatorTool(),
        category="productivity",
        tags=["math", "calculate", "arithmetic", "expression"],
        aliases=["math_tool"],
    )
    catalog.register_tool(
        GetCurrentTimeTool(),
        category="productivity",
        tags=["time", "date", "timezone", "clock", "now"],
        aliases=["clock"],
    )
    catalog.register_tool(
        DataVisualizerTool(),
        category="data/visualization",
        tags=["chart", "graph", "plot", "bar", "line", "pie"],
        aliases=["chart_tool", "plot_tool"],
    )
    catalog.register_tool(
        MarkdownPreviewerTool(),
        category="data/visualization",
        tags=["markdown", "preview", "render", "document"],
        aliases=["md_preview"],
    )
    catalog.register_tool(
        JsonExplorerTool(),
        category="data/exploration",
        tags=["json", "tree", "inspect", "parse", "data"],
        aliases=["json_viewer"],
    )
    catalog.register_tool(
        ColorPaletteTool(),
        category="data/visualization",
        tags=["color", "palette", "contrast", "wcag", "harmony"],
        aliases=["color_tool"],
    )
    catalog.register_tool(
        KanbanBoardTool(),
        category="development/project",
        tags=["kanban", "board", "drag", "column", "task"],
        aliases=["project_board"],
    )
    catalog.register_tool(
        SpotifyPlayerTool(spotify_service=spotify_svc),
        category="media",
        tags=["music", "play", "song", "track", "spotify", "stream", "audio"],
        aliases=["music_player"],
    )
    if code_interpreter_tool:
        catalog.register_tool(
            code_interpreter_tool,
            category="development/execution",
            tags=["python", "bash", "code", "execute", "run", "script"],
            aliases=["code_exec", "sandbox"],
        )

    # Chain executor + pipeline manager tools (registered after catalog is populated)
    # These require runtime deps created below — placeholders registered now,
    # actual deps injected after adapter infrastructure init.
    from raavan.catalog.tools.chain_executor.tool import ChainExecutorTool
    from raavan.catalog.tools.pipeline_manager.tool import PipelineManagerTool

    # Create sentinel instances — real deps are injected after infra init below
    app.state._chain_executor_cls = ChainExecutorTool
    app.state._pipeline_manager_cls = PipelineManagerTool

    app.state.tools = catalog

    # HITL configuration for the agent
    # Note: tool_approval_handler is set per-request in _get_agent_deps using
    # the per-thread bridge acquired from bridge_registry.
    app.state.tools_requiring_approval = [
        e.name for e in app.state.tools.by_risk(ToolRisk.CRITICAL)
    ]
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

    # ── Adapter infrastructure (DataRef, Chains, Pipelines, Temporal, Triggers) ──
    from raavan.catalog._data_ref import DataRefStore
    from raavan.catalog._chain_runtime import ChainRuntime
    from raavan.catalog._pipeline import PipelineEngine, PipelineStore
    from raavan.catalog._triggers.scheduler import TriggerScheduler
    from raavan.catalog._triggers.webhooks import WebhookRegistry
    from raavan.catalog._triggers.conditions import ConditionMonitor

    # DataRefStore — zero-context-bloat data exchange (Redis + optional S3)
    data_store = DataRefStore(redis_url=settings.REDIS_URL)
    await data_store.connect()
    app.state.data_store = data_store

    # ChainRuntime — LLM-written code-based adapter chaining
    chain_runtime = ChainRuntime(catalog=catalog, data_store=data_store)
    app.state.chain_runtime = chain_runtime

    # PipelineEngine + PipelineStore — declarative saved adapter chains
    pipeline_engine = PipelineEngine(catalog=catalog, data_store=data_store)
    pipeline_store = PipelineStore(session_factory=session_factory)
    app.state.pipeline_engine = pipeline_engine
    app.state.pipeline_store = pipeline_store

    # Temporal — durable workflow orchestration (optional, graceful if unavailable)
    temporal_host = os.environ.get("TEMPORAL_HOST", "localhost:7233")
    try:
        from raavan.catalog._temporal.client import TemporalClient

        temporal = TemporalClient(host=temporal_host)
        await temporal.connect()
        app.state.temporal = temporal
        logging.getLogger(__name__).info("Temporal connected at %s", temporal_host)
    except Exception as exc:
        app.state.temporal = None
        logging.getLogger(__name__).warning(
            "Temporal unavailable (%s) — workflow routes disabled", exc
        )

    # Triggers — autonomous scheduling (cron/interval, webhooks, conditions)
    trigger_scheduler = TriggerScheduler(redis_url=settings.REDIS_URL)
    webhook_registry = WebhookRegistry()
    condition_monitor = ConditionMonitor()

    if app.state.temporal:
        trigger_scheduler.set_temporal(app.state.temporal)
        webhook_registry.set_temporal(app.state.temporal)
        condition_monitor.set_temporal(app.state.temporal)

    try:
        await trigger_scheduler.start()
    except Exception as exc:
        logging.getLogger(__name__).warning("TriggerScheduler failed to start: %s", exc)

    app.state.trigger_scheduler = trigger_scheduler
    app.state.webhook_registry = webhook_registry
    app.state.condition_monitor = condition_monitor

    # Now register chain/pipeline tools with their real dependencies
    chain_executor_tool = app.state._chain_executor_cls(chain_runtime=chain_runtime)
    catalog.register_tool(
        chain_executor_tool,
        category="development/execution",
        tags=["chain", "pipe", "automate", "script", "workflow", "adapter"],
        aliases=["chain_tool", "adapter_chain"],
    )
    pipeline_manager_tool = app.state._pipeline_manager_cls(
        pipeline_engine=pipeline_engine,
        pipeline_store=pipeline_store,
    )
    catalog.register_tool(
        pipeline_manager_tool,
        category="development/execution",
        tags=["pipeline", "save", "run", "workflow", "automation"],
        aliases=["pipeline_tool"],
    )

    # Typed context — new code should prefer app.state.ctx over individual attrs.
    # Existing routes continue to work via the app.state.* assignments above.
    app.state.ctx = ServerContext(
        model_client=app.state.model_client,
        audio_client=app.state.audio_client,
        redis_memory=app.state.redis_memory,
        tools=app.state.tools,
        bridge_registry=app.state.bridge_registry,
        tools_requiring_approval=app.state.tools_requiring_approval,
        system_instructions=app.state.system_instructions,
        tool_timeout=app.state.tool_timeout,
        cancel_registry=app.state.cancel_registry,
        mcp_servers=app.state.mcp_servers,
        session_factory=app.state.session_factory,
        ci_client=app.state.ci_client,
        file_store=app.state.file_store,
    )

    # Quiet noisy loggers
    for name in ("httpx", "urllib3", "openai"):
        logging.getLogger(name).setLevel(logging.WARNING)

    yield

    # ---------- SHUTDOWN ----------
    # Triggers
    if getattr(app.state, "trigger_scheduler", None):
        await app.state.trigger_scheduler.stop()
    if getattr(app.state, "condition_monitor", None):
        await app.state.condition_monitor.stop()
    # DataRefStore
    if getattr(app.state, "data_store", None):
        await app.state.data_store.disconnect()
    if getattr(app.state, "file_store", None):
        await app.state.file_store.shutdown()
    if getattr(app.state, "ci_client", None):
        await app.state.ci_client.close()  # type: ignore[union-attr]
    if getattr(app.state, "redis_memory", None):
        await app.state.redis_memory.disconnect()
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
    app.include_router(tasks_router)
    app.include_router(pipelines_router)
    app.include_router(workflows_router)
    app.include_router(triggers_router)

    # Visual Builder — only mounted when ENABLE_BUILDER=true (zero prod footprint)
    if settings.ENABLE_BUILDER:
        from raavan.server.routes.builder import router as builder_router

        app.include_router(builder_router)
        logging.getLogger(__name__).info("Builder API mounted at /builder")

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
