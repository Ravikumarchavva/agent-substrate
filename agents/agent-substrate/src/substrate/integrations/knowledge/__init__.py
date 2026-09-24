"""substrate.integrations.knowledge — Retrieval-Augmented Generation primitives."""

from __future__ import annotations


from substrate.kernel.storage.graph import Entity, GraphStore, Relationship, SubGraph
from substrate.kernel.storage.vector import Document, SearchResult, VectorStore
from substrate.integrations.knowledge.pipeline import RAGPipeline
from substrate.integrations.knowledge.graph_rag import GraphRAGPipeline
from substrate.integrations.knowledge.page_pipeline import PageIndexRAGPipeline
from substrate.integrations.knowledge.protocol import RAGProvider
from substrate.integrations.knowledge.loaders.pdf_loader import PDFLoader
from substrate.integrations.knowledge.chunking import (
    TextChunker,
    SentenceChunker,
    PageChunker,
    ExtractionDocumentChunker,
    get_chunker,
)
from substrate.integrations.knowledge.reranker import LLMReranker
from substrate.integrations.knowledge.ask import ask, AskResult, Citation, list_catalog

# document_ingest_pipeline.DocumentIngestPipeline/ExtractionFailedError are
# deliberately NOT re-exported here: zero production callers (confirmed via
# repo-wide grep — only its own test file uses it), so nothing should be
# able to pick it up as the package's "the" ingest pipeline by importing
# from this top-level namespace. Import directly from
# substrate.integrations.knowledge.document_ingest_pipeline if you
# specifically want it.

__all__ = [
    "GraphStore",
    "VectorStore",
    "Document",
    "Entity",
    "Relationship",
    "SearchResult",
    "SubGraph",
    "RAGPipeline",
    "GraphRAGPipeline",
    "PageIndexRAGPipeline",
    "RAGProvider",
    "PDFLoader",
    "TextChunker",
    "SentenceChunker",
    "PageChunker",
    "ExtractionDocumentChunker",
    "get_chunker",
    "LLMReranker",
    "ask",
    "AskResult",
    "Citation",
    "list_catalog",
]
