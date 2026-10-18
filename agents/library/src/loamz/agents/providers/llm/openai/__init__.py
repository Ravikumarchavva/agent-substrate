"""loamz.agents.providers.llm.openai — OpenAI Responses API adapter.

``OpenAIClient``
    Uses the OpenAI **Responses API** (``/v1/responses``).
    Supports file uploads, vision, audio, realtime, and structured outputs.
    Best for: OpenAI itself.

``OpenAIEmbeddingClient``
    OpenAI text-embedding models.

For the universal OpenAI-compatible client (Groq, Ollama, vLLM, Together, …)
see ``loamz.agents.kernel.models.OpenAICompatibleClient``.
"""

from __future__ import annotations

from loamz.agents.providers.llm.openai.openai_client import OpenAIClient
from loamz.agents.providers.llm.openai.openai_embedding_client import OpenAIEmbeddingClient

__all__ = [
    "OpenAIClient",
    "OpenAIEmbeddingClient",
]
