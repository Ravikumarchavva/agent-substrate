"""substrate.capabilities.knowledge — Retrieval-Augmented Generation primitives."""

from __future__ import annotations


from substrate.kernel.storage.graph import Entity, GraphStore, Relationship, SubGraph
from substrate.kernel.storage.vector import Document, SearchResult, VectorStore
from substrate.capabilities.knowledge.pipeline import RAGPipeline
from substrate.capabilities.knowledge.graph_rag import GraphRAGPipeline
from substrate.capabilities.knowledge.page_pipeline import PageIndexRAGPipeline
from substrate.capabilities.knowledge.protocol import RAGProvider
from substrate.capabilities.knowledge.loaders.pdf_loader import PDFLoader
from substrate.capabilities.knowledge.chunking import (
    TextChunker,
    SentenceChunker,
    PageChunker,
    ExtractionDocumentChunker,
    get_chunker,
)
from substrate.capabilities.knowledge.document_store import InMemoryDocumentStore
from substrate.capabilities.knowledge.reranker import LLMReranker
from substrate.capabilities.knowledge.ask import ask, AskResult, Citation, list_catalog

# document_ingest_pipeline.DocumentIngestPipeline/ExtractionFailedError are
# deliberately NOT re-exported here: zero production callers (confirmed via
# repo-wide grep — only its own test file uses it), so nothing should be
# able to pick it up as the package's "the" ingest pipeline by importing
# from this top-level namespace. Import directly from
# substrate.capabilities.knowledge.document_ingest_pipeline if you
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
    "InMemoryDocumentStore",
    "LLMReranker",
    "ask",
    "AskResult",
    "Citation",
    "list_catalog",
]
