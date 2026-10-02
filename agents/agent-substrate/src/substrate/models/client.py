"""LLM client re-exports — canonical Protocol definitions live in substrate.models."""

from __future__ import annotations

from substrate.models.protocols import EmbeddingModel, ChatModel

__all__ = ["ChatModel", "EmbeddingModel"]
