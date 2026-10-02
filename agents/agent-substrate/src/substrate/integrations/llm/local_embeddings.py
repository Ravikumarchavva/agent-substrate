"""sentence-transformers embedding client — the canonical default EmbeddingModel.

Implements the ``EmbeddingModel`` kernel Protocol using the
``sentence-transformers`` library.  Runs entirely on CPU — no API key,
no external server required, ever.  The model is downloaded from HuggingFace
on first instantiation and cached in ``~/.cache/huggingface/``. The L1
default for the same reason the local stores are:
needs the least infrastructure the Protocol can possibly need.

Usage::

    from substrate.integrations.llm import SentenceTransformersEmbeddingClient

    # Default: all-MiniLM-L6-v2 (384-dim, fast on CPU)
    client = SentenceTransformersEmbeddingClient()

    # Higher quality (768-dim, slower)
    client = SentenceTransformersEmbeddingClient("all-mpnet-base-v2")

Via factory (for other providers, e.g. a real API-backed embedding model)::

    from substrate.integrations.llm import create_embedding_client

    client = create_embedding_client("sentence-transformers/all-MiniLM-L6-v2")
"""

from __future__ import annotations

import asyncio
import logging

from substrate.integrations.llm.base import BaseEmbeddingClient
from substrate.models import EmbeddingResult

logger = logging.getLogger(__name__)


class SentenceTransformersEmbeddingClient(BaseEmbeddingClient):
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
        from sentence_transformers import SentenceTransformer  # pyright: ignore[reportMissingImports]

        if device is None:
            try:
                import torch  # pyright: ignore[reportMissingImports]

                device = "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                device = "cpu"

        logger.info("Loading sentence-transformers model %s on %s", model, device)
        self._model = SentenceTransformer(model, device=device)
        self._batch_size = batch_size
        self._device = device
        dimensions = getattr(self._model, "get_sentence_embedding_dimension", lambda: None)()
        super().__init__(model, dimensions, max_input_tokens=int(getattr(self._model, "max_seq_length", 0) or 256))

    async def _embed_texts(self, texts: list[str], *, query: bool) -> EmbeddingResult:
        loop = asyncio.get_running_loop()
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
            or self.model
        )
        return EmbeddingResult(embeddings=raw.tolist(), model=model_name)
