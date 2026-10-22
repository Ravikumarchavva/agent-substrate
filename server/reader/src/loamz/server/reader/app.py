"""The document reader: a standalone FastAPI service that reads uploads into `ExtractionResult`s.

Deployed as its own low-replica service (the heavy PaddlePaddle and llama.cpp runtime, models loaded). Loamz Data
reaches it with `Reader("http://…")` (`loamz.engine.documents`) when `LOAMZ_DATA_DOCUMENT_READER_URL` is set;
otherwise the storage Reader reads in-process (no layout model, no chart and table crops from a layout pass).

On boot it detects the hardware, resolves which extraction mode this pod can serve given that hardware and the
configured mode (`autoconfig.py`), and builds that one engine, degrading rather than failing when the requested mode
cannot be satisfied (`engines/factory.py`). `/v1/health` reports what was requested and what runs.

    uvicorn loamz.server.reader.app:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from loamz.engine.models.llama.hardware import detect as detect_hardware

from .autoconfig import resolve_runtime
from .config import ServiceConfig
from .engines.factory import build_engine
from .engines.native import NativeEngine
from .routes import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
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
        "Starting document reader  pod=%s  requested_mode=%s  "
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
