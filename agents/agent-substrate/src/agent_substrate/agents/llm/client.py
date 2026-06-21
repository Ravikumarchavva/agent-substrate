"""LLM client re-exports — canonical Protocol definitions live in agent_substrate.kernel.llm."""

from __future__ import annotations

from agent_substrate.kernel.llm import LLMClient, EmbeddingClient

__all__ = ["LLMClient", "EmbeddingClient"]
