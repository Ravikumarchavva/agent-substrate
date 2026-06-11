"""ravi.capabilities.llm — default LLM client for the framework.

``OpenAIChatCompletionClient`` implements the standard OpenAI Chat Completions
API (``/v1/chat/completions``) and works with virtually every modern LLM
provider — cloud or local::

    from ravi.capabilities.llm import OpenAIChatCompletionClient

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

For provider-auto-wiring use ``LLMFactory`` from ``ravi.integrations.llm``.
"""

from __future__ import annotations

from ravi.capabilities.llm.chat_client import OpenAIChatCompletionClient
from ravi.capabilities.llm.sentence_transformers_embedding_client import (
    SentenceTransformersEmbeddingClient,
)

__all__ = ["OpenAIChatCompletionClient", "SentenceTransformersEmbeddingClient"]
