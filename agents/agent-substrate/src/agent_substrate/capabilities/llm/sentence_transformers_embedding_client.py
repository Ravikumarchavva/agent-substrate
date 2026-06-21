"""sentence-transformers embedding client.

Implements the ``EmbeddingClient`` kernel Protocol using the
``sentence-transformers`` library.  Runs entirely on CPU — no API key,
no external server required.  The model is downloaded from HuggingFace
on first instantiation and cached in ``~/.cache/huggingface/``.

Usage::

    from agent_substrate.capabilities.llm import SentenceTransformersEmbeddingClient

    # Default: all-MiniLM-L6-v2 (384-dim, fast on CPU)
    client = SentenceTransformersEmbeddingClient()

    # Higher quality (768-dim, slower)
    client = SentenceTransformersEmbeddingClient("all-mpnet-base-v2")

Via factory (recommended)::

    from agent_substrate.integrations.llm import create_embedding_client

    client = create_embedding_client("sentence-transformers/all-MiniLM-L6-v2")
"""

from __future__ import annotations

import asyncio
import logging

from agent_substrate.kernel.llm import EmbeddingResult

logger = logging.getLogger(__name__)


class SentenceTransformersEmbeddingClient:
    """Embedding client backed by sentence-transformers (CPU or CUDA).

    Args:
        model: Model name or local path.
        batch_size: Texts per encode call. 64 is safe on CPU; use 512+ on GPU.
        device: ``"cuda"``, ``"cpu"``, or ``None`` (auto-detect).
    """

    def __init__(
        self,
        model: str = "all-MiniLM-L6-v2",
        batch_size: int = 64,
        device: str | None = None,
    ) -> None:
        from sentence_transformers import SentenceTransformer

        if device is None:
            try:
                import torch

                device = "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                device = "cpu"

        logger.info("Loading sentence-transformers model %s on %s", model, device)
        self._model = SentenceTransformer(model, device=device)
        self._batch_size = batch_size
        self._device = device

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(
            None,
            lambda: self._model.encode(
                texts,
                batch_size=self._batch_size,
                normalize_embeddings=True,  # required for cosine similarity with pgvector <=>
                show_progress_bar=False,
            ),
        )
        model_name = (
            getattr(getattr(self._model, "model_card_data", None), "model_name", None)
            or "sentence-transformers"
        )
        return EmbeddingResult(embeddings=raw.tolist(), model=model_name)

    async def embed_single(self, text: str) -> list[float]:
        result = await self.embed([text])
        return result.embeddings[0]
