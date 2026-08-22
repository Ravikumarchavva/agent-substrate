"""substrate.runtimes.embedding_reranker — multimodal embedding and
reranking (Qwen3-VL-Embedding-2B / Qwen3-VL-Reranker-2B via the
llama-embed/llama-rerank llama-server sidecars), and the lightweight
client for calling it. See embedding_reranker/service/ for the FastAPI
microservice itself; client.py has no heavy dependencies and is always
importable from the base install.
"""

from __future__ import annotations

from substrate.runtimes.embedding_reranker.client import EmbeddingRerankerClient

__all__ = ["EmbeddingRerankerClient"]
