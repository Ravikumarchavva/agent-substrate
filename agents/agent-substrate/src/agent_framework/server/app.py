"""Production FastAPI application for the chat server.

Replaces the old ``main.py`` with proper:
  - Database lifecycle (init / shutdown)
  - OpenTelemetry setup
  - Router mounting
  - CORS middleware
  - Health endpoint
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from agent_framework.configs.settings import settings
from agent_framework.model_clients.openai.openai_client import OpenAIClient
from agent_framework.observability.telemetry import (
    configure_opentelemetry,
    shutdown_opentelemetry,
)
from agent_framework.server.database import close_db, get_session_factory, init_db
from agent_framework.server.routes.chat import router as chat_router
from agent_framework.server.routes.feedback import router as feedback_router
from agent_framework.server.routes.threads import router as threads_router
from agent_framework.tools.builtin_tools import CalculatorTool, GetCurrentTimeTool


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
    app.state.tools = [CalculatorTool(), GetCurrentTimeTool()]
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
        "- Use | pipes and a separator row\n"
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
    app.include_router(feedback_router)

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
