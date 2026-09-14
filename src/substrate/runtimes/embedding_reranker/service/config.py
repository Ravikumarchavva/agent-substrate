"""Environment-based configuration for the embedding-reranker service.

All settings are read from environment variables with the
``EMBEDDING_RERANKER_`` prefix.
"""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings


class ServiceConfig(BaseSettings):
    """Embedding-reranker service configuration."""

    # ── Server ───────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8080

    # ── Inter-service auth ───────────────────────────────────────────────
    auth_token: str = ""

    # ── Multimodal embedding + reranker ─────────────────────────────────
    # Qwen3-VL-Embedding-2B / Qwen3-VL-Reranker-2B, served by the
    # llama-embed/llama-rerank sidecars (docker-compose.yml) — see
    # docs/claude_docs/decisions.md for why these replaced SigLIP + MiniLM
    # cross-encoder loaded in-process. embedding_dim=2048 is the model's
    # native output width, verified via a real embed call, not assumed.
    embed_server_url: str = "http://llama-embed:8031"
    rerank_server_url: str = "http://llama-rerank:8032"
    embedding_dim: int = 2048

    # ── Deployment mode ──────────────────────────────────────────────────
    # "remote" (default, unchanged behavior) -- embed_server_url/
    # rerank_server_url above point at pre-existing sidecar containers
    # (docker-compose.yml's llama-embed/llama-rerank services), exactly as
    # before this field existed. "local" -- this process spawns and
    # supervises its OWN llama-embed/llama-rerank children via the shared
    # LocalLlamaServerPool (runtimes/inference_pool/llama_pool.py, promoted
    # out of document_intelligence this session) instead of reaching a
    # sidecar over the network. See the local-mode fields below and
    # app.py's lifespan for the real wiring.
    #
    # Known, out-of-scope gap: LocalLlamaServerPool's argv builder
    # (llama_pool.py::_argv) is hardcoded to the flag shape
    # document_intelligence's PaddleOCR-VL worker needs -- it always
    # passes `-m <main_gguf> --mmproj <mmproj_gguf>` and has no mechanism
    # to add `--embedding --pooling last` (llama-embed's real flags) or
    # `--reranking` (llama-rerank's), nor to use `--hf-repo`/`--hf-file`
    # runtime download the way docker-compose.yml's sidecars do. Local
    # mode below is real, working plumbing -- hardware detection, VRAM
    # admission, pool lifecycle, EmbeddingReranker wiring -- but a spawned
    # child will only behave as a genuine embed/rerank server if
    # embed_main_gguf/rerank_main_gguf point at GGUF files that already
    # bake in the right serving behavior; closing this gap for real would
    # need an additive change to llama_pool.py's argv construction, which
    # this task was explicitly scoped to leave untouched.
    mode: Literal["remote", "local"] = "remote"

    # ── mode "local" only ────────────────────────────────────────────────
    llama_server_bin: str = "llama-server"
    embed_main_gguf: str = "/models/embedding-reranker/qwen3-vl-embedding-2b.gguf"
    embed_mmproj_gguf: str = "/models/embedding-reranker/qwen3-vl-embedding-2b-mmproj.gguf"
    rerank_main_gguf: str = "/models/embedding-reranker/qwen3-vl-reranker-2b.gguf"
    rerank_mmproj_gguf: str = "/models/embedding-reranker/qwen3-vl-reranker-2b-mmproj.gguf"
    embed_base_port: int = 8090
    rerank_base_port: int = 8095
    # 2048 mirrors the real --ctx-size docker-compose.yml passes to both
    # llama-embed and llama-rerank today.
    local_ctx_size: int = 2048
    local_startup_timeout_s: float = 300.0
    local_max_restarts: int = 5

    # ── Pod identity (k8s Downward API) ──────────────────────────────────
    pod_name: str = "embedding-reranker-0"

    model_config = {"env_prefix": "EMBEDDING_RERANKER_"}
