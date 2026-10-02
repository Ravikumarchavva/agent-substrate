"""Document protocols — contracts for reading documents: the extractor and the OCR engine."""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from substrate.documents.types import ExtractionResult, OcrResult, Strategy


@runtime_checkable
class DocumentExtractor(Protocol):
    """Contract every document reader satisfies — the built-in one, a document server, or your own.

    ``read`` never raises for a document it cannot read: it returns ``ExtractionResult(success=False, error=…)``.
    ``content_type`` is a hint (the format is sniffed from the bytes first); ``strategy`` is ``fast | auto | hi_res | ocr_only``.
    """

    async def read(
        self, data: bytes, filename: str, *, content_type: str | None = None, strategy: Strategy = "auto"
    ) -> ExtractionResult: ...


@runtime_checkable
class Ocr(Protocol):
    """Contract for an OCR engine: one page image in, its text out.

    ``recognize`` is synchronous (it runs in a worker, off the event loop) and must not raise on an unreadable image — return an
    empty result. Instances are rebuilt in the worker from their constructor arguments, so they must be cheap to construct.
    """

    name: str

    def recognize(self, png: bytes, *, languages: Sequence[str] = ("eng",)) -> OcrResult: ...


__all__ = [
    "Ocr",
    "DocumentExtractor",
]
