"""substrate.capabilities.knowledge.loaders — Document loaders for various file formats."""

from __future__ import annotations

from substrate.capabilities.knowledge.loaders.base import (
    BaseDocumentLoader,
    DocumentLoaderRegistry,
)
from substrate.capabilities.knowledge.loaders.csv_loader import CSVLoader
from substrate.capabilities.knowledge.loaders.json_loader import JSONLoader
from substrate.capabilities.knowledge.loaders.pdf_loader import PDFLoader
from substrate.capabilities.knowledge.loaders.text_loader import TextLoader

__all__ = [
    "BaseDocumentLoader",
    "DocumentLoaderRegistry",
    "CSVLoader",
    "JSONLoader",
    "PDFLoader",
    "TextLoader",
]
