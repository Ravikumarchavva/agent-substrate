"""substrate.capabilities.llm — default LLM client for the framework.

``OpenAIChatCompletionClient`` implements the standard OpenAI Chat Completions
API (``/v1/chat/completions``) and works with virtually every modern LLM
provider — cloud or local::

    from substrate.capabilities.llm import OpenAIChatCompletionClient

    # Ollama running locally
    client = OpenAIChatCompletionClient(
        model="llama3.2",
        api_key="ollama",
        base_url="http://localhost:11434/v1",
    )

    # Groq cloud
    client = OpenAIChatCompletionClient(
        model="llama-3.3-70b-versatile",
        api_key=groq_key,
        base_url="https://api.groq.com/openai/v1",
    )

For provider-auto-wiring use ``LLMFactory`` from ``substrate.integrations.llm``.
"""

from __future__ import annotations

from substrate.capabilities.llm.chat_client import OpenAIChatCompletionClient
from substrate.capabilities.llm.sentence_transformers_embedding_client import (
    SentenceTransformersEmbeddingClient,
)
from substrate.capabilities.llm.openai_embedding_client import OpenAIEmbeddingClient

__all__ = [
    "OpenAIChatCompletionClient",
    "SentenceTransformersEmbeddingClient",
    "OpenAIEmbeddingClient",
]
