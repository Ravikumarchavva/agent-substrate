"""Models storage calls: the embedder and reranker ports, and clients for ones reached by URL."""

from loamz.data.storage.models.protocols import EmbeddingModel, EmbeddingResult, Modality, Reranker
from loamz.data.storage.models.remote import RemoteEmbedder, RemoteReranker

__all__ = [
    "EmbeddingModel",
    "EmbeddingResult",
    "Modality",
    "RemoteEmbedder",
    "RemoteReranker",
    "Reranker",
]
