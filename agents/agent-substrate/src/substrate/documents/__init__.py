"""substrate.documents — read what a user hands an agent (``Reader``), and let a model navigate it (``Library``)."""

from __future__ import annotations

from substrate.documents.library import (
    Added,
    DocumentError,
    DocumentInfo,
    Hit,
    Library,
    Listing,
    Outline,
    Passage,
    Picture,
    SectionInfo,
)
from substrate.documents.protocols import DocumentExtractor, Ocr
from substrate.documents.reader import Reader
from substrate.documents.tool import DocumentsTool
from substrate.documents.types import (
    ExtractedImage,
    ExtractedImageLabel,
    ExtractedPage,
    ExtractionResult,
    OcrResult,
    PageMethod,
    ReadLimits,
    Strategy,
)

__all__ = [
    "Added",
    "DocumentError",
    "DocumentExtractor",
    "DocumentInfo",
    "DocumentsTool",
    "ExtractedImage",
    "ExtractedImageLabel",
    "ExtractedPage",
    "ExtractionResult",
    "Hit",
    "Library",
    "Listing",
    "Ocr",
    "OcrResult",
    "Outline",
    "PageMethod",
    "Passage",
    "Picture",
    "ReadLimits",
    "Reader",
    "SectionInfo",
    "Strategy",
]
