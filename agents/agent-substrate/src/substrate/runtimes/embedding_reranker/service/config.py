"""Environment-based configuration for the embedding-reranker service.

All settings are read from environment variables with the
``EMBEDDING_RERANKER_`` prefix.
"""

from __future__ import annotations

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

    # ── Pod identity (k8s Downward API) ──────────────────────────────────
    pod_name: str = "embedding-reranker-0"

    model_config = {"env_prefix": "EMBEDDING_RERANKER_"}
