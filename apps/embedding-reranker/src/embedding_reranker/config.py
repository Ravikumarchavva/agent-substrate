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
    # LocalLlamaServerPool's argv builder (llama_pool.py::_argv) accepts
    # an `extra_args` list appended after its universal flags, and
    # main_gguf/mmproj_gguf are optional -- this service passes
    # --hf-repo/--hf-file/--embedding/--pooling/--reranking through
    # extra_args instead (see app.py's lifespan), the same real mechanism
    # docker-compose.yml's llama-embed/llama-rerank sidecars already use.
    mode: Literal["remote", "local"] = "remote"

    # ── mode "local" only ────────────────────────────────────────────────
    llama_server_bin: str = "llama-server"
    # --hf-repo/--hf-file (llama-server's own runtime-download mechanism),
    # not a local GGUF path -- matches the real, already-working values
    # docker-compose.yml's llama-embed/llama-rerank sidecars use today
    # (see their service blocks). Unlike document_intelligence's VL model,
    # there's no local build-time quantization step for these two --
    # llama-server downloads and caches the file itself on first boot
    # (LLAMA_CACHE env var / the "llama-model-cache" compose volume).
    embed_hf_repo: str = "Rizwan313/Qwen3-VL-Embedding-2B-GGUF"
    embed_hf_file: str = "qwen3-vl-embedding-2b-Q4_K_M.gguf"
    rerank_hf_repo: str = "staralt/Qwen3-VL-Reranker-2B-Q4_K_M-GGUF"
    rerank_hf_file: str = "qwen3-vl-reranker-2b-q4_k_m-imat.gguf"
    embed_base_port: int = 8090
    rerank_base_port: int = 8095
    # Mirrors docker-compose.yml's real --parallel values for each sidecar
    # (llama-embed: 2, llama-rerank: 1) -- see llama_pool.py's own
    # docstring: `slots` already covers `-np`/`--parallel` (same flag,
    # different spelling), so these feed the pool's existing `slots` param
    # rather than needing a `--parallel` entry in extra_args.
    embed_slots: int = 2
    rerank_slots: int = 1
    # 2048 mirrors the real --ctx-size docker-compose.yml passes to both
    # llama-embed and llama-rerank today.
    local_ctx_size: int = 2048
    local_startup_timeout_s: float = 300.0
    local_max_restarts: int = 5

    # ── Pod identity (k8s Downward API) ──────────────────────────────────
    pod_name: str = "embedding-reranker-0"

    model_config = {"env_prefix": "EMBEDDING_RERANKER_"}
