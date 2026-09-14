"""substrate.runtimes.inference_pool — spawn/health-check/dispatch/shutdown
of N local ``llama-server``-shaped subprocess children (or a pre-configured
remote deployment), shared across every consumer that talks to a local-or-
remote OpenAI-compatible inference backend.

Promoted out of ``document_intelligence/service/`` (its original, sole
consumer) once ``embedding_reranker`` became a real second one — see
``docs/claude_docs/decisions.md`` for the "why promote now" reasoning.
Placed here, a sibling of ``document_intelligence``/``embedding_reranker``,
because it's the only legal home reachable from ``serving/``,
``capabilities/``, and ``runtimes/`` alike without a new import-linter
exception (``runtimes/`` is already exempt from the "serving cannot import
agents/capabilities" contract).
"""

from __future__ import annotations

from substrate.runtimes.inference_pool.llama_pool import (
    LocalLlamaServerPool,
    RemoteInferencePool,
)
from substrate.runtimes.inference_pool.pool_types import InferencePool, PoolWorker

__all__ = [
    "LocalLlamaServerPool",
    "RemoteInferencePool",
    "InferencePool",
    "PoolWorker",
]
