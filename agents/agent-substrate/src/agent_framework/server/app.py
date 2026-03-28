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

from agent_framework.configs.settings import settings
from agent_framework.core.memory.redis_memory import RedisMemory
from agent_framework.tools.human_input import AskHumanTool
from agent_framework.integrations.llm.openai.openai_client import OpenAIClient
from agent_framework.integrations.audio.openai import OpenAIAudioClient
from agent_framework.shared.observability.telemetry import (
    configure_opentelemetry,
    shutdown_opentelemetry,
)
from agent_framework.server.database import close_db, get_session_factory, init_db
from agent_framework.server.context import ServerContext
from agent_framework.server.routes.admin import router as admin_router
from agent_framework.server.routes.auth import router as auth_router
from agent_framework.server.routes.cancel import router as cancel_router
from agent_framework.server.routes.chat import router as chat_router
from agent_framework.server.routes.code_interpreter import (
    router as code_interpreter_router,
)
from agent_framework.server.routes.elements import router as elements_router
from agent_framework.server.routes.feedback import router as feedback_router
from agent_framework.server.routes.audio import router as audio_router
from agent_framework.server.routes.files import router as files_router
from agent_framework.server.routes.hitl import router as hitl_router
from agent_framework.server.routes.mcp_apps import router as mcp_apps_router
from agent_framework.server.routes.spotify_oauth import router as spotify_oauth_router
from agent_framework.server.routes.tasks import router as tasks_router
from agent_framework.server.routes.threads import router as threads_router
from agent_framework.core.tools.base_tool import ToolRisk
from agent_framework.core.tools.builtin_tools import CalculatorTool, GetCurrentTimeTool
from agent_framework.core.tools.registry import ToolRegistry
from agent_framework.core.storage.factory import create_file_store
from agent_framework.tools.code_interpreter import CodeInterpreterTool
from agent_framework.tools.code_interpreter.http_client import (
    CodeInterpreterClient,
)
from agent_framework.tools.file_manager_tool import FileManagerTool
from agent_framework.integrations.mcp.app_tools import (
    ColorPaletteTool,
    DataVisualizerTool,
    JsonExplorerTool,
    KanbanBoardTool,
    MarkdownPreviewerTool,
    SpotifyPlayerTool,
)
from agent_framework.integrations.spotify.client import SpotifyService
from agent_framework.server.sse.bridge import BridgeRegistry
from agent_framework.tools.task_manager_tool import TaskManagerTool

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
    from agent_framework.tools.task_manager_tool import (
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
    ask_tool = AskHumanTool(handler=None, max_requests_per_run=5)
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

    app.state.tools = ToolRegistry.from_list(
        [
            ask_tool,
            task_tool,
            file_manager_tool,
            CalculatorTool(),
            GetCurrentTimeTool(),
            DataVisualizerTool(),
            MarkdownPreviewerTool(),
            JsonExplorerTool(),
            ColorPaletteTool(),
            KanbanBoardTool(),
            SpotifyPlayerTool(spotify_service=spotify_svc),
            *([code_interpreter_tool] if code_interpreter_tool else []),
        ]
    )

    # HITL configuration for the agent
    # Note: tool_approval_handler is set per-request in _get_agent_deps using
    # the per-thread bridge acquired from bridge_registry.
    app.state.tools_requiring_approval = [
        t.name for t in app.state.tools if t.risk == ToolRisk.CRITICAL
    ]
    app.state.tool_timeout = 300.0  # match HITL bridge timeout

    _prompt_path = (
        __import__("pathlib").Path(__file__).parent / "prompts" / "default_system.md"
    )
    app.state.system_instructions = _prompt_path.read_text(encoding="utf-8").strip()

    # Cancel registry: maps thread_id → asyncio.Event so running streams can
    # be aborted from the POST /chat/{thread_id}/cancel endpoint.
    app.state.cancel_registry: dict[str, object] = {}

    # MCP server registry: maps server_id → RegistryMcpServer dict.
    # Populated at runtime via POST /builder/mcp-servers (in-memory, not persisted).
    app.state.mcp_servers: dict[str, dict] = {}

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
    if getattr(app.state, "file_store", None):
        await app.state.file_store.shutdown()
    if getattr(app.state, "ci_client", None):
        await app.state.ci_client.close()
    if getattr(app.state, "redis_memory", None):
        await app.state.redis_memory.disconnect()
    for tool in app.state.tools:
        if hasattr(tool, "stop"):
            try:
                await tool.stop()
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

    # Visual Builder — only mounted when ENABLE_BUILDER=true (zero prod footprint)
    if settings.ENABLE_BUILDER:
        from agent_framework.server.routes.builder import router as builder_router

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
