"""substrate.documents — read what a user hands an agent (``Reader``), and let a model navigate it (``Library``)."""

from __future__ import annotations

from substrate.documents.enrichment import (
    Described,
    DocumentBrief,
    Enriched,
    Enricher,
    EnrichmentUsage,
    FiledDocument,
    FirstSentenceEnricher,
    Organised,
    Organiser,
    Reorganised,
    SectionBrief,
)
from substrate.documents.bundle import Bundle
from substrate.documents.library import Library
from substrate.documents.llm_enricher import LLMEnricher
from substrate.documents.llm_organiser import LLMOrganiser
from substrate.documents.protocols import DocumentExtractor, DocumentLibrary, Ocr
from substrate.documents.reader import Reader
from substrate.documents.tool import DocumentsTool
from substrate.documents.types import (
    Added,
    DocumentError,
    DocumentInfo,
    EnrichmentState,
    ExtractedImage,
    ExtractedImageLabel,
    ExtractedPage,
    ExtractionResult,
    Hit,
    Hits,
    Listing,
    OcrResult,
    Outline,
    PageMethod,
    Passage,
    Picture,
    ReadLimits,
    SectionInfo,
    Strategy,
    Topic,
)

__all__ = [
    "Added",
    "Bundle",
    "Described",
    "DocumentBrief",
    "DocumentError",
    "DocumentExtractor",
    "DocumentInfo",
    "DocumentLibrary",
    "DocumentsTool",
    "Enriched",
    "Enricher",
    "EnrichmentState",
    "EnrichmentUsage",
    "ExtractedImage",
    "ExtractedImageLabel",
    "ExtractedPage",
    "ExtractionResult",
    "FiledDocument",
    "FirstSentenceEnricher",
    "Hit",
    "Hits",
    "LLMEnricher",
    "LLMOrganiser",
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
    "Organised",
    "Organiser",
    "Reorganised",
    "Topic",
]
