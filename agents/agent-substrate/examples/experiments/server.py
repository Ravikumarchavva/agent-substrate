"""Real-World Agent Mimics — Document Intelligence Pipeline

Demonstrates ravi-engine framework patterns in a realistic scenario:

    Upload → ProxyAgent → OCRAgent → ┬─ PIIAgent
                                     ├─ ClassifierAgent
                                     └─ EmbeddingAgent

Framework features showcased:
  • BaseTool subclasses for each agent's capability
  • AgentCatalog for tool registration and discovery
  • Pub/sub via async EventBus (fan-out to parallel subscribers)
  • PIIDetectionGuardrail reused from ravi.kernel.guardrails
  • LazyTool for optional heavy deps (sentence-transformers)
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import tools as _tools
from routes import router
from ravi.logger import setup_logging

logger = setup_logging(mode="pretty", handler="console")

# Prevents CUDA allocator fragmentation during vLLM profiling.
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _llm = None
    try:
        from vllm.v1.engine.async_llm import AsyncLLM
        from vllm.engine.arg_utils import AsyncEngineArgs
        from mineru_vl_utils import MinerUClient, MinerULogitsProcessor

        _llm = AsyncLLM.from_engine_args(
            AsyncEngineArgs(
                model="opendatalab/MinerU2.5-2509-1.2B",
                logits_processors=[MinerULogitsProcessor],
                # ── memory: fit on small GPUs (3-4 GB) ──────────────────────
                max_model_len=4096,  # invoices are short; 16 k wastes KV cache
                gpu_memory_utilization=0.90,
                enforce_eager=False,  # skip CUDA-graph capture (~300 MB saved)
                # ── disable video profiling (the direct cause of the OOM) ───
                limit_mm_per_prompt={"image": 1, "video": 0},
            )
        )
        _tools._mineru_client = MinerUClient(
            backend="vllm-async-engine", vllm_async_llm=_llm
        )
        logger.info("MinerU vLLM engine ready (model=MinerU2.5-2509-1.2B)")
    except ImportError:
        logger.warning(
            "mineru-vl-utils / vllm not installed — OCR falls back to CLI subprocess"
        )

    yield

    if _llm is not None:
        _llm.shutdown()
        logger.info("MinerU vLLM engine shut down")


def create_app() -> FastAPI:
    app = FastAPI(title="Real-World Agent Mimics", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8765, reload=True)
