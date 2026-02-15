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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from agent_framework.configs.settings import settings
from agent_framework.human_input import AskHumanTool
from agent_framework.model_clients.openai.openai_client import OpenAIClient
from agent_framework.observability.telemetry import (
    configure_opentelemetry,
    shutdown_opentelemetry,
)
from agent_framework.server.database import close_db, get_session_factory, init_db
from agent_framework.server.routes.chat import router as chat_router
from agent_framework.server.routes.elements import router as elements_router
from agent_framework.server.routes.feedback import router as feedback_router
from agent_framework.server.routes.hitl import router as hitl_router
from agent_framework.server.routes.mcp_apps import router as mcp_apps_router
from agent_framework.server.routes.spotify_oauth import router as spotify_oauth_router
from agent_framework.server.routes.threads import router as threads_router
from agent_framework.tools.builtin_tools import CalculatorTool, GetCurrentTimeTool
from agent_framework.tools.mcp_app_tools import (
    ColorPaletteTool,
    DataVisualizerTool,
    JsonExplorerTool,
    KanbanBoardTool,
    MarkdownPreviewerTool,
    SpotifyPlayerTool,
)
from agent_framework.services.spotify import SpotifyService
from agent_framework.web_hitl import WebHITLBridge


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""

    # ---------- STARTUP ----------
    # Observability
    configure_opentelemetry(
        service_name="agent-framework",
        otlp_trace_endpoint="localhost:4318",
    )

    # Database
    await init_db(settings.DATABASE_URL, echo=False)

    # Shared agent dependencies (injected into routes via app.state)
    app.state.model_client = OpenAIClient(
        model="gpt-4o-mini",
        api_key=settings.OPENAI_API_KEY,
    )

    # HITL bridge: connects agent approval/input requests to SSE/HTTP
    bridge = WebHITLBridge(response_timeout=300.0)
    app.state.bridge = bridge

    # AskHumanTool wired through the bridge
    ask_tool = AskHumanTool(
        handler=bridge.human_handler,
        max_requests_per_run=5,
    )

    # Spotify service (needs SPOTIFY_CLIENT_ID + SPOTIFY_CLIENT_SECRET)
    spotify_svc = None
    if settings.SPOTIFY_CLIENT_ID and settings.SPOTIFY_CLIENT_SECRET:
        spotify_svc = SpotifyService(
            client_id=settings.SPOTIFY_CLIENT_ID,
            client_secret=settings.SPOTIFY_CLIENT_SECRET,
        )

    app.state.tools = [
        ask_tool,
        CalculatorTool(),
        GetCurrentTimeTool(),
        DataVisualizerTool(),
        MarkdownPreviewerTool(),
        JsonExplorerTool(),
        ColorPaletteTool(),
        KanbanBoardTool(),
        SpotifyPlayerTool(spotify_service=spotify_svc),
    ]

    # HITL configuration for the agent
    app.state.tool_approval_handler = bridge.approval_handler
    app.state.tools_requiring_approval = ["calculator", "get_current_time"]
    app.state.tool_timeout = 300.0  # match HITL bridge timeout

    app.state.system_instructions = (
        "You are a helpful AI assistant. "
        "You MUST format all math using Markdown LaTeX.\n\n"
        "Rules:\n"
        "- Inline math: $...$\n"
        "- Block math: $$...$$\n"
        "- Do NOT escape dollar signs\n"
        "- Do NOT use \\[ \\] or \\( \\)\n"
        "When the user asks for a table:\n"
        "- ALWAYS return a Markdown table\n"
        "- Use | pipes and a separator row\n\n"
        "When you need user preferences or confirmation, use the ask_human tool\n"
        "to present options and let them choose.\n\n"
        "When the user asks you to visualize, chart, or plot data, use the\n"
        "data_visualizer tool. Provide the data as an array of {label, value}\n"
        "objects. The user will see an interactive chart they can switch\n"
        "between bar, line, and pie views.\n\n"
        "When showing structured data (API responses, configs, nested objects),\n"
        "use the json_explorer tool so the user can browse it interactively.\n\n"
        "When displaying formatted text, documentation, or rich content,\n"
        "use the markdown_previewer tool for a rendered preview.\n\n"
        "When working with colors, themes, or palettes, use the\n"
        "color_palette tool to show interactive color swatches.\n\n"
        "When managing tasks, projects, or workflows, use the\n"
        "kanban_board tool to display a drag-and-drop board.\n\n"
        "When the user asks about music, songs, artists, or wants to listen\n"
        "to something, use the spotify_player tool. Provide a descriptive\n"
        "search query. The user will see an interactive music player with\n"
        "30-second previews, play/pause, and next/previous controls.\n\n"
        "IMPORTANT: When you use any of the interactive tools above\n"
        "(data_visualizer, json_explorer, markdown_previewer, color_palette,\n"
        "kanban_board, spotify_player), the user will see a rich interactive\n"
        "UI widget. After calling one of these tools, give ONLY a brief\n"
        "1-2 sentence confirmation. Do NOT repeat, summarize, or list the\n"
        "data you passed to the tool — the user can already see it in the\n"
        "interactive widget.\n"
    )

    # Expose session factory for routes that need a fresh DB session
    app.state.session_factory = get_session_factory()

    # Quiet noisy loggers
    for name in ("httpx", "urllib3", "openai"):
        logging.getLogger(name).setLevel(logging.WARNING)

    yield

    # ---------- SHUTDOWN ----------
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

    # CORS – allow all for dev, tighten in production
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount routers
    app.include_router(threads_router)
    app.include_router(chat_router)
    app.include_router(hitl_router)
    app.include_router(elements_router)
    app.include_router(feedback_router)
    app.include_router(mcp_apps_router)
    app.include_router(spotify_oauth_router)

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

    uvicorn.run(app, host="0.0.0.0", port=8001)
