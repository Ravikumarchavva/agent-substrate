"""Standalone FastAPI application for the document-extraction service.

Deploy this as its own low-replica Deployment (heavy paddlepaddle OCR
runtime, model-loaded pods — see deployment/k8s/base/runtime/extraction.yaml).
The main backend calls it via HTTP through ExtractionClient
(capabilities/knowledge/extraction_client.py), only when
EXTRACTION_SERVICE_URL is configured; otherwise chat attachments fall back to
the lightweight pypdf path for PDFs and the local RAG backend has no
chart-image or multimodal-embedding capability.

Usage::

    uvicorn substrate.serving.services.extraction.app:app \
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
from .embedding import EmbeddingReranker
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
    """Build the extraction pipeline, embedding model, and reranker on boot,
    and warm each with a tiny synthetic input so the first real request
    isn't also paying first-load latency."""
    svc_config = ServiceConfig()
    logger.info(
        "Starting extraction service  pod=%s  ocr_size=%s  device=%s",
        svc_config.pod_name,
        svc_config.ocr_size,
        svc_config.device,
    )

    pipeline = ExtractionPipeline(
        ocr_size=svc_config.ocr_size, device=svc_config.device
    )
    embedding_reranker = EmbeddingReranker(
        embed_server_url=svc_config.embed_server_url,
        rerank_server_url=svc_config.rerank_server_url,
    )

    app.state.pipeline = pipeline
    app.state.embedding_reranker = embedding_reranker
    app.state.config = svc_config
    app.state.start_time = time.monotonic()

    try:
        pipeline.warmup()
        await embedding_reranker.warmup()
    except Exception as exc:
        # Warmup is best-effort — a failure here must not block startup;
        # real requests still trigger a (slower, one-time) model load /
        # sidecar round-trip.
        logger.info("Extraction service warmup skipped (%s)", exc)

    logger.info("Extraction service ready  pod=%s", svc_config.pod_name)

    yield

    await embedding_reranker.aclose()
    logger.info("Extraction service stopped  pod=%s", svc_config.pod_name)


def create_app() -> FastAPI:
    """Build and return the FastAPI application."""
    application = FastAPI(
        title="Document Extraction Service",
        version="1.0.0",
        description=(
            "Layout-aware document parsing (PDF layout, chart/table "
            "detection, OCR), multimodal embedding, and reranking for chat "
            "attachments and RAG — isolated from the main API process due "
            "to its heavy paddlepaddle OCR runtime footprint."
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
