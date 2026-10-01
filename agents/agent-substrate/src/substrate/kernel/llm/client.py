"""LLM client re-exports — canonical Protocol definitions live in substrate.kernel.llm."""

from __future__ import annotations

from substrate.kernel.llm import LLMClient, EmbeddingClient

__all__ = ["LLMClient", "EmbeddingClient"]
