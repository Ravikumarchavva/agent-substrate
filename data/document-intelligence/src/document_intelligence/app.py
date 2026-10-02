"""Standalone FastAPI application for the document-intelligence service.

Deploy this as its own low-replica service (heavy paddlepaddle/llama.cpp
runtime, model-loaded — see docker-compose.yml's `document-intelligence`
profile, or the `document-intelligence-gpu` variant). The main backend
reaches it with ``Reader("http://…")``
(substrate.documents), only when DOCUMENT_INTELLIGENCE_SERVICE_URL is
configured; otherwise the library's own Reader reads in-process (no layout
model, no chart/table crops from a layout pass). Multimodal embedding/reranking is
a separate service now — see apps/embedding-reranker/.

On boot: detect real hardware (hardware.py) -> resolve which extraction
mode this pod should actually serve given that hardware and
ServiceConfig.mode (autoconfig.py) -> construct that one engine, degrading
gracefully rather than failing if the requested mode can't be satisfied
(engines/factory.py). See /v1/health for what was requested vs. what's
actually running.

Usage::

    uvicorn document_intelligence.app:app \
        --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from substrate.logger import setup_logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .autoconfig import resolve_runtime
from .config import ServiceConfig
from .engines.factory import build_engine
from .engines.native import NativeEngine
from inference_pool.hardware import detect as detect_hardware
from .routes import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
setup_logging(
    service_name="document-intelligence"
)  # the application, not the library, configures logging
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Detect hardware, resolve the runtime, build that engine, and warm it
    with a tiny synthetic input so the first real request isn't also
    paying first-load latency."""
    svc_config = ServiceConfig()
    hw = detect_hardware()
    resolved = resolve_runtime(svc_config, hw)

    logger.info(
        "Starting document-intelligence service  pod=%s  requested_mode=%s  "
        "resolved_mode=%s  worker_count=%d  gpus=%d  degraded_from=%s",
        svc_config.pod_name,
        svc_config.mode,
        resolved.mode,
        resolved.worker_count,
        len(hw.gpus),
        resolved.degraded_from,
    )
    for reason in resolved.reasons:
        logger.info("autoconfig: %s", reason)

    engine = await build_engine(svc_config, resolved)

    app.state.engine = engine
    # What reads every non-PDF format, and the fallback when a layout engine fails (the engine itself when this pod has none).
    app.state.native = (
        engine
        if isinstance(engine, NativeEngine)
        else NativeEngine(max_bytes=svc_config.max_upload_bytes)
    )
    app.state.config = svc_config
    app.state.hardware = hw
    app.state.resolved = resolved
    app.state.start_time = time.monotonic()

    try:
        engine.warmup()
    except Exception as exc:
        # Warmup is best-effort — a failure here must not block startup;
        # real requests still trigger a (slower, one-time) model load.
        logger.info("Document-intelligence service warmup skipped (%s)", exc)

    logger.info(
        "Document-intelligence service ready  pod=%s  engine=%s",
        svc_config.pod_name,
        engine.name,
    )

    yield

    await engine.aclose()
    logger.info("Document-intelligence service stopped  pod=%s", svc_config.pod_name)


def create_app() -> FastAPI:
    """Build and return the FastAPI application."""
    application = FastAPI(
        title="Document Intelligence Service",
        version="1.0.0",
        description=(
            "Layout-aware document parsing (PDF layout, chart/table "
            "detection, OCR/VL recognition, DOCX/PPTX conversion) for chat "
            "attachments and RAG — isolated from the main API process due "
            "to its heavy paddlepaddle/llama.cpp runtime footprint."
        ),
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(router)
    return application


app = create_app()
