"""LLM client re-exports — canonical Protocol definitions live in substrate.kernel.abstractions.llm."""

from __future__ import annotations

from substrate.kernel.abstractions.llm import EmbeddingClient, LLMClient

__all__ = ["LLMClient", "EmbeddingClient"]
