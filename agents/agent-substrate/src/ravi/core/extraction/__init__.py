"""core.extraction — Structured data extraction framework.

Combines the ``parse()`` primitive with domain-specific schemas and batch
processing for enterprise use cases like invoice extraction, receipt
parsing, and document classification.
"""

from __future__ import annotations

from ravi.core.extraction.extractor import Extractor
from ravi.core.extraction.schemas import (
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
