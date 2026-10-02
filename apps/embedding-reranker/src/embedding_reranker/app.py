"""Standalone FastAPI application for the embedding-reranker service.

Deploy this as its own low-replica service — no local model, just a thin
httpx proxy to the llama-embed/llama-rerank sidecars, so resource needs are
much lighter than document_intelligence (see docker-compose.yml's
`embedding-reranker` profile). The main backend calls
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
from typing import Any

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

# Estimated, not verified this session (unlike document_intelligence's
# factory.py::_VL_WORKER_VRAM_BUDGET_MIB, which IS a real measured number)
# -- Qwen3-VL-Embedding-2B / Qwen3-VL-Reranker-2B are both ~2B-parameter
# Q4_K_M GGUFs (see docker-compose.yml's llama-embed/llama-rerank --hf-file
# values), so ~1.5-2GB resident weights plus ctx=2048 KV cache and runtime
# overhead is a conservative ballpark, not a benchmarked figure the way the
# VL worker's 3500 MiB budget was. Treat as a placeholder to replace with a
# real measurement before relying on this in production.
_EMBED_WORKER_VRAM_BUDGET_MIB = 2500
_RERANK_WORKER_VRAM_BUDGET_MIB = 2500


async def _admit_local_gpu(ledger: Any, hw: Any, budget_mib: int, *, label: str) -> str:
    """Pick a single device string for one local worker: the first detected
    GPU if its real free VRAM (per ``hardware.detect()``) covers ``budget_mib``
    once reserved against the shared ledger, else ``"cpu"``. Mirrors
    document_intelligence's ``engines/factory.py::_admit_gpu_workers``
    admission pattern, simplified to the single-worker-per-pool case this
    service needs (embed and rerank each get exactly one worker) -- never
    raises, a failed reservation degrades to CPU rather than risking a real
    CUDA OOM crash."""
    if not hw.gpus:
        return "cpu"
    device = f"gpu:{hw.gpus[0].index}"
    lease = await ledger.reserve(device, budget_mib)
    if lease is None:
        logger.warning(
            "embedding-reranker local mode: %s worker on %s skipped -- real "
            "free VRAM does not cover the estimated %d MiB budget -- "
            "degrading to CPU rather than risking a real CUDA OOM crash.",
            label,
            device,
            budget_mib,
        )
        return "cpu"
    return device


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build the embedding/reranker client on boot, and warm it with a tiny
    synthetic input so the first real request isn't also paying first-load
    latency.

    mode="remote" (default): byte-identical to this service's original
    behavior -- no hardware detection, no pool construction, just the two
    configured sidecar URLs.

    mode="local": spawns two independently-supervised LocalLlamaServerPool
    instances (embed, rerank) via the shared inference_pool machinery,
    acquires exactly one worker from each for this service's whole
    lifetime (this service has exactly one consumer of each pool, so a
    persistent single-worker checkout -- never released -- is the correct,
    simple pattern here, unlike document_intelligence's per-request
    acquire/release), and feeds each worker's endpoint URL into the same
    EmbeddingReranker constructor used in remote mode.
    """
    svc_config = ServiceConfig()
    logger.info(
        "Starting embedding-reranker service  pod=%s  mode=%s",
        svc_config.pod_name,
        svc_config.mode,
    )

    embed_pool = None
    rerank_pool = None

    if svc_config.mode == "local":
        from substrate.runtimes.document_intelligence.service.hardware import detect
        from substrate.runtimes.inference_pool.llama_pool import LocalLlamaServerPool
        from substrate.runtimes.inference_pool.vram_ledger import VramLedger

        hw = detect()
        ledger = VramLedger({f"gpu:{g.index}": g.free_mib for g in hw.gpus})

        embed_device = await _admit_local_gpu(
            ledger, hw, _EMBED_WORKER_VRAM_BUDGET_MIB, label="embed"
        )
        rerank_device = await _admit_local_gpu(
            ledger, hw, _RERANK_WORKER_VRAM_BUDGET_MIB, label="rerank"
        )

        # main_gguf/mmproj_gguf stay None -- these two serve via
        # llama-server's own --hf-repo/--hf-file runtime download (the
        # real, already-working mechanism docker-compose.yml's sidecars
        # use), passed through extra_args instead. --embedding/--reranking
        # are llama-server's actual real flags for these two model roles
        # (verified against docker-compose.yml's own llama-embed/
        # llama-rerank command blocks, not guessed); slots= (not
        # extra_args) covers --parallel, see config.py's own comment.
        embed_pool = LocalLlamaServerPool(
            binary=svc_config.llama_server_bin,
            gpu_devices=[embed_device],
            base_port=svc_config.embed_base_port,
            slots=svc_config.embed_slots,
            ctx_size=svc_config.local_ctx_size,
            startup_timeout_s=svc_config.local_startup_timeout_s,
            max_restarts=svc_config.local_max_restarts,
            model_name="compatible/Qwen3-VL-Embedding-2B",
            extra_args=[
                "--hf-repo",
                svc_config.embed_hf_repo,
                "--hf-file",
                svc_config.embed_hf_file,
                "--embedding",
                "--pooling",
                "last",
            ],
        )
        rerank_pool = LocalLlamaServerPool(
            binary=svc_config.llama_server_bin,
            gpu_devices=[rerank_device],
            base_port=svc_config.rerank_base_port,
            slots=svc_config.rerank_slots,
            ctx_size=svc_config.local_ctx_size,
            startup_timeout_s=svc_config.local_startup_timeout_s,
            max_restarts=svc_config.local_max_restarts,
            model_name="compatible/Qwen3-VL-Reranker-2B",
            extra_args=[
                "--hf-repo",
                svc_config.rerank_hf_repo,
                "--hf-file",
                svc_config.rerank_hf_file,
                "--reranking",
            ],
        )
        await embed_pool.start()
        await rerank_pool.start()

        embed_worker = await embed_pool.acquire()
        rerank_worker = await rerank_pool.acquire()
        embed_url = embed_worker.endpoint.base_url
        rerank_url = rerank_worker.endpoint.base_url
    else:
        embed_url = svc_config.embed_server_url
        rerank_url = svc_config.rerank_server_url

    embedding_reranker = EmbeddingReranker(
        embed_server_url=embed_url,
        rerank_server_url=rerank_url,
    )

    app.state.embedding_reranker = embedding_reranker
    app.state.config = svc_config
    app.state.start_time = time.monotonic()
    app.state.embed_pool = embed_pool
    app.state.rerank_pool = rerank_pool

    try:
        await embedding_reranker.warmup()
    except Exception as exc:
        # Warmup is best-effort — a failure here must not block startup;
        # real requests still trigger a (slower, one-time) sidecar round-trip.
        logger.info("Embedding-reranker service warmup skipped (%s)", exc)

    logger.info("Embedding-reranker service ready  pod=%s", svc_config.pod_name)

    yield

    await embedding_reranker.aclose()
    if svc_config.mode == "local":
        assert embed_pool is not None and rerank_pool is not None
        await embed_pool.aclose()
        await rerank_pool.aclose()
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
