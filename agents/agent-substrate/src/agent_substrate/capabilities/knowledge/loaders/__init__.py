"""agent_substrate.capabilities.knowledge.loaders — Document loaders for various file formats."""

from __future__ import annotations

from agent_substrate.capabilities.knowledge.loaders.base import (
    BaseDocumentLoader,
    DocumentLoaderRegistry,
)
from agent_substrate.capabilities.knowledge.loaders.csv_loader import CSVLoader
from agent_substrate.capabilities.knowledge.loaders.docling_loader import DoclingLoader
from agent_substrate.capabilities.knowledge.loaders.json_loader import JSONLoader
from agent_substrate.capabilities.knowledge.loaders.pdf_loader import PDFLoader
from agent_substrate.capabilities.knowledge.loaders.text_loader import TextLoader

__all__ = [
    "BaseDocumentLoader",
    "DocumentLoaderRegistry",
    "CSVLoader",
    "DoclingLoader",
    "JSONLoader",
    "PDFLoader",
    "TextLoader",
]
