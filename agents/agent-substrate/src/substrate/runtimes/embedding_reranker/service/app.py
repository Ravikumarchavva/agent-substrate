"""Standalone FastAPI application for the embedding-reranker service.

Deploy this as its own low-replica Deployment — no local model, just a thin
httpx proxy to the llama-embed/llama-rerank sidecars, so resource needs are
much lighter than document_intelligence (see
deployment/k8s/base/runtime/embedding-reranker.yaml). The main backend calls
it via HTTP through EmbeddingRerankerClient
(runtimes/embedding_reranker/client.py), only when
EMBEDDING_RERANKER_SERVICE_URL is configured; otherwise image ingestion and
reranking are unavailable.

Usage::

    uvicorn substrate.runtimes.embedding_reranker.service.app:app \
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
from .routes import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build the embedding/reranker client on boot, and warm it with a tiny
    synthetic input so the first real request isn't also paying first-load
    latency."""
    svc_config = ServiceConfig()
    logger.info(
        "Starting embedding-reranker service  pod=%s",
        svc_config.pod_name,
    )

    embedding_reranker = EmbeddingReranker(
        embed_server_url=svc_config.embed_server_url,
        rerank_server_url=svc_config.rerank_server_url,
    )

    app.state.embedding_reranker = embedding_reranker
    app.state.config = svc_config
    app.state.start_time = time.monotonic()

    try:
        await embedding_reranker.warmup()
    except Exception as exc:
        # Warmup is best-effort — a failure here must not block startup;
        # real requests still trigger a (slower, one-time) sidecar round-trip.
        logger.info("Embedding-reranker service warmup skipped (%s)", exc)

    logger.info("Embedding-reranker service ready  pod=%s", svc_config.pod_name)

    yield

    await embedding_reranker.aclose()
    logger.info("Embedding-reranker service stopped  pod=%s", svc_config.pod_name)


def create_app() -> FastAPI:
    """Build and return the FastAPI application."""
    application = FastAPI(
        title="Embedding + Reranking Service",
        version="1.0.0",
        description=(
            "Multimodal embedding and reranking (Qwen3-VL-Embedding-2B / "
            "Qwen3-VL-Reranker-2B via the llama-embed/llama-rerank "
            "llama-server sidecars) for RAG — a thin proxy, isolated from "
            "the main API process only so its sidecar dependencies are "
            "independently deployable and scalable."
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
