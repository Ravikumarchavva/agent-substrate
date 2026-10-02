"""substrate.integrations.knowledge — Retrieval-Augmented Generation primitives."""

from __future__ import annotations


from substrate.stores import Entity, GraphStore, Relationship, SubGraph
from substrate.stores import Document, SearchResult, VectorStore
from substrate.integrations.knowledge.pipeline import RAGPipeline
from substrate.integrations.knowledge.graph_rag import GraphRAGPipeline
from substrate.integrations.knowledge.page_pipeline import PageIndexRAGPipeline
from substrate.integrations.knowledge.protocol import RAGProvider
from substrate.integrations.knowledge.chunking import (
    TextChunker,
    SentenceChunker,
    PageChunker,
    get_chunker,
)
from substrate.integrations.knowledge.reranker import LLMReranker
from substrate.integrations.knowledge.ask import ask, AskResult, Citation, list_catalog

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
    "TextChunker",
    "SentenceChunker",
    "PageChunker",
    "get_chunker",
    "LLMReranker",
    "ask",
    "AskResult",
    "Citation",
    "list_catalog",
]
