"""agent_substrate.integrations.llm.openai — OpenAI Responses API adapter.

``OpenAIClient``
    Uses the OpenAI **Responses API** (``/v1/responses``).
    Supports file uploads, vision, audio, realtime, and structured outputs.
    Best for: OpenAI itself.

``OpenAIEmbeddingClient``
    OpenAI text-embedding models.

For the universal OpenAI-compatible client (Groq, Ollama, vLLM, Together, …)
see ``agent_substrate.capabilities.llm.OpenAIChatCompletionClient``.
"""

from __future__ import annotations

from agent_substrate.integrations.llm.openai.openai_client import OpenAIClient
from agent_substrate.integrations.llm.openai.openai_embedding_client import OpenAIEmbeddingClient

__all__ = [
    "OpenAIClient",
    "OpenAIEmbeddingClient",
]
