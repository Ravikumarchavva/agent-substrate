"""ravi.integrations.llm.openai — OpenAI Responses API adapter.

``OpenAIClient``
    Uses the OpenAI **Responses API** (``/v1/responses``).
    Supports file uploads, vision, audio, realtime, and structured outputs.
    Best for: OpenAI itself.

``OpenAIEmbeddingClient``
    OpenAI text-embedding models.

For the universal OpenAI-compatible client (Groq, Ollama, vLLM, Together, …)
see ``ravi.capabilities.llm.OpenAIChatCompletionClient``.
"""

from __future__ import annotations

from ravi.integrations.llm.openai.openai_client import OpenAIClient
from ravi.integrations.llm.openai.openai_embedding_client import OpenAIEmbeddingClient

__all__ = [
    "OpenAIClient",
    "OpenAIEmbeddingClient",
]
