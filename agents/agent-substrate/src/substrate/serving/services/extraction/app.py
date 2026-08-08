"""Standalone FastAPI application for the Docling extraction service.

Deploy this as its own low-replica Deployment (heavy torch/CUDA runtime,
model-loaded pods — see deployment/k8s/base/runtime/docling.yaml). The
main backend calls it via HTTP through DoclingClient
(capabilities/knowledge/docling_client.py), only when DOCLING_SERVICE_URL
is configured; otherwise chat attachments fall back to the lightweight
pypdf path for PDFs.

Usage::

    uvicorn substrate.serving.services.docling.app:app \
        --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from substrate.logger import setup_logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from substrate.capabilities.knowledge.loaders.docling_loader import DoclingLoader

from .config import ServiceConfig
from .routes import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build the DoclingLoader on boot (holds the DocumentConverter
    configuration) and warm the underlying model weights with a tiny
    synthetic document, so the first real request isn't also paying
    first-load latency."""
    svc_config = ServiceConfig()
    logger.info(
        "Starting Docling service  pod=%s  ocr_enabled=%s",
        svc_config.pod_name,
        svc_config.ocr_enabled,
    )

    loader = DoclingLoader(
        output_format=svc_config.output_format,
        ocr_enabled=svc_config.ocr_enabled,
    )

    app.state.loader = loader
    app.state.config = svc_config
    app.state.start_time = time.monotonic()

    try:
        await loader.load(b"%PDF-1.4\n%%EOF", metadata={"source": "_warmup.pdf"})
    except Exception as exc:
        # Warmup is best-effort — a malformed synthetic PDF failing to
        # convert must not block startup; real requests still trigger a
        # (slower, one-time) model load on their own.
        logger.info("Docling warmup skipped (%s)", exc)

    logger.info("Docling service ready  pod=%s", svc_config.pod_name)

    yield

    logger.info("Docling service stopped  pod=%s", svc_config.pod_name)


def create_app() -> FastAPI:
    """Build and return the FastAPI application."""
    application = FastAPI(
        title="Docling Extraction Service",
        version="1.0.0",
        description=(
            "Structure-aware document parsing (PDF/DOCX/PPTX layout, "
            "tables, OCR) for chat attachments — isolated from the main "
            "API process due to its heavy torch/CUDA runtime footprint."
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
