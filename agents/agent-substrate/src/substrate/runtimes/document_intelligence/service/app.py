"""Standalone FastAPI application for the document-intelligence service.

Deploy this as its own low-replica Deployment (heavy paddlepaddle OCR
runtime, model-loaded pods — see
deployment/k8s/base/runtime/document-intelligence.yaml). The main backend
calls it via HTTP through ExtractionClient
(runtimes/document_intelligence/client.py), only when
DOCUMENT_INTELLIGENCE_SERVICE_URL is configured; otherwise chat attachments
fall back to the lightweight pypdf path for PDFs and the local RAG backend
has no chart-image extraction capability. Multimodal embedding/reranking is
a separate service now — see runtimes/embedding_reranker/.

Usage::

    uvicorn substrate.runtimes.document_intelligence.service.app:app \
        --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from substrate.logger import setup_logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import ServiceConfig
from .pipeline import ExtractionPipeline
from .routes import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build the extraction pipeline on boot, and warm it with a tiny
    synthetic input so the first real request isn't also paying first-load
    latency."""
    svc_config = ServiceConfig()
    logger.info(
        "Starting document-intelligence service  pod=%s  ocr_size=%s  device=%s",
        svc_config.pod_name,
        svc_config.ocr_size,
        svc_config.device,
    )

    pipeline = ExtractionPipeline(
        ocr_size=svc_config.ocr_size, device=svc_config.device
    )

    app.state.pipeline = pipeline
    app.state.config = svc_config
    app.state.start_time = time.monotonic()

    try:
        pipeline.warmup()
    except Exception as exc:
        # Warmup is best-effort — a failure here must not block startup;
        # real requests still trigger a (slower, one-time) model load.
        logger.info("Document-intelligence service warmup skipped (%s)", exc)

    logger.info("Document-intelligence service ready  pod=%s", svc_config.pod_name)

    yield

    logger.info("Document-intelligence service stopped  pod=%s", svc_config.pod_name)


def create_app() -> FastAPI:
    """Build and return the FastAPI application."""
    application = FastAPI(
        title="Document Intelligence Service",
        version="1.0.0",
        description=(
            "Layout-aware document parsing (PDF layout, chart/table "
            "detection, OCR) for chat attachments and RAG — isolated from "
            "the main API process due to its heavy paddlepaddle OCR "
            "runtime footprint."
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
