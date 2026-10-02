"""LLM client re-exports — canonical Protocol definitions live in substrate.models."""

from __future__ import annotations

from substrate.models.protocols import EmbeddingClient, LLMClient

__all__ = ["LLMClient", "EmbeddingClient"]
