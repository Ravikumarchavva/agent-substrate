"""Re-export shim for ``ExtractionPipeline``.

The single-engine ``ExtractionPipeline`` that used to live in this module
moved to ``engines/paddle_classic.py`` as ``PaddleClassicEngine`` (mode
``ocr_classic``), byte-for-byte the same behavior, now implementing the
``PaginatedExtractionEngine`` Protocol. DTOs (``ExtractedImage``/
``ExtractedPage``/``ExtractionResult``) are kernel's own
(``substrate.kernel.document``) — every engine speaks that shared contract
directly now, no service-internal duplicate.

This module re-exports both under their old names so anything importing
``ExtractionPipeline``/``ExtractedImage``/etc. from here keeps working.
New code should import from ``substrate.kernel.document``/
``engines.paddle_classic`` directly.
"""

from __future__ import annotations

from substrate.runtimes.document_intelligence.service.engines.paddle_classic import (
    PaddleClassicEngine as ExtractionPipeline,
)
from substrate.kernel.document import ExtractedImage, ExtractedPage, ExtractionResult

__all__ = ["ExtractedImage", "ExtractedPage", "ExtractionResult", "ExtractionPipeline"]
