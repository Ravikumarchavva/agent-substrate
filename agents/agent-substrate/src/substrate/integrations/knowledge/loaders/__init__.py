"""substrate.integrations.knowledge.loaders — Document loaders for various file formats."""

from __future__ import annotations

from substrate.integrations.knowledge.loaders.base import (
    BaseDocumentLoader,
    DocumentLoaderRegistry,
)
from substrate.integrations.knowledge.loaders.csv_loader import CSVLoader
from substrate.integrations.knowledge.loaders.json_loader import JSONLoader
from substrate.integrations.knowledge.loaders.pdf_loader import PDFLoader
from substrate.integrations.knowledge.loaders.text_loader import TextLoader

__all__ = [
    "BaseDocumentLoader",
    "DocumentLoaderRegistry",
    "CSVLoader",
    "JSONLoader",
    "PDFLoader",
    "TextLoader",
]
