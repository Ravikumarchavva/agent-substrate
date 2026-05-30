"""core.extraction — Structured data extraction framework.

Combines the ``parse()`` primitive with domain-specific schemas and batch
processing for enterprise use cases like invoice extraction, receipt
parsing, and document classification.
"""

from __future__ import annotations

from ravi.reasoning.extraction.extractor import Extractor
from ravi.reasoning.extraction.schemas import (
    BusinessCard,
    Contract,
    Invoice,
    Receipt,
    Resume,
)

__all__ = [
    "BusinessCard",
    "Contract",
    "Extractor",
    "Invoice",
    "Receipt",
    "Resume",
]
