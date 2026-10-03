"""substrate.documents — read what a user hands an agent (``Reader``), and let a model navigate it (``Library``)."""

from __future__ import annotations

from substrate.documents.enrichment import (
    Described,
    DocumentBrief,
    Enriched,
    Enricher,
    EnrichmentUsage,
    FirstSentenceEnricher,
    SectionBrief,
)
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
    Topic,
)
from substrate.documents.llm_enricher import LLMEnricher
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
    "Described",
    "DocumentBrief",
    "DocumentError",
    "DocumentExtractor",
    "DocumentInfo",
    "DocumentsTool",
    "Enriched",
    "Enricher",
    "EnrichmentUsage",
    "ExtractedImage",
    "ExtractedImageLabel",
    "ExtractedPage",
    "ExtractionResult",
    "FirstSentenceEnricher",
    "Hit",
    "LLMEnricher",
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
    "SectionBrief",
    "SectionInfo",
    "Strategy",
    "Topic",
]
