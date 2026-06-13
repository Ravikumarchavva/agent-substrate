"""ravi.capabilities.knowledge.loaders — Document loaders for various file formats."""

from __future__ import annotations

from ravi.capabilities.knowledge.loaders.base import (
    BaseDocumentLoader,
    DocumentLoaderRegistry,
)
from ravi.capabilities.knowledge.loaders.csv_loader import CSVLoader
from ravi.capabilities.knowledge.loaders.docling_loader import DoclingLoader
from ravi.capabilities.knowledge.loaders.json_loader import JSONLoader
from ravi.capabilities.knowledge.loaders.pdf_loader import PDFLoader
from ravi.capabilities.knowledge.loaders.text_loader import TextLoader

__all__ = [
    "BaseDocumentLoader",
    "DocumentLoaderRegistry",
    "CSVLoader",
    "DoclingLoader",
    "JSONLoader",
    "PDFLoader",
    "TextLoader",
]
