"""Back-compat re-export shim.

The single-engine ``ExtractionPipeline``/DTOs that used to live in this
module were split apart for the 3-mode redesign:

- DTOs (``ExtractedImage``/``ExtractedPage``/``ExtractionResult``) moved to
  ``types.py``, shared by every engine.
- The PPStructureV3 pipeline itself moved to ``engines/paddle_classic.py``
  as ``PaddleClassicEngine`` (mode ``ocr_classic``), byte-for-byte the same
  behavior, now implementing the ``PaginatedExtractionEngine`` Protocol.

This module re-exports both under their old names so anything importing
``ExtractionPipeline``/``ExtractedImage``/etc. from here keeps working
unchanged. New code should import from ``types``/``engines.paddle_classic``
directly.
"""

from __future__ import annotations

from substrate.runtimes.document_intelligence.service.engines.paddle_classic import (
    PaddleClassicEngine as ExtractionPipeline,
)
from substrate.runtimes.document_intelligence.service.types import (
    ExtractedImage,
    ExtractedPage,
    ExtractionResult,
)

__all__ = ["ExtractedImage", "ExtractedPage", "ExtractionResult", "ExtractionPipeline"]
