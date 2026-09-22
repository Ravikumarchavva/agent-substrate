"""``ExtractionEngine`` — the extraction-mode extensibility point.

Grounded in how two real, proven document-conversion libraries structure
this same problem (read directly from their installed/published source this
session, not guessed):

- markitdown's ``DocumentConverter`` (``_base_converter.py``): two abstract
  methods, ``accepts(file_stream, stream_info) -> bool`` and
  ``convert(file_stream, stream_info) -> DocumentConverterResult``,
  deliberately matching signatures so a successful ``accepts()`` reliably
  predicts ``convert()`` will succeed.
- docling's ``AbstractDocumentBackend`` (``abstract_backend.py``): a root
  class (``is_valid()``, ``supported_formats()`` classmethod, ``unload()``)
  that splits into ``PaginatedDocumentBackend`` (page-by-page, feeds a
  downstream recognition/OCR pipeline) vs ``DeclarativeDocumentBackend``
  (converts straight to the document model, no recognition pipeline needed
  — for formats that are already structured).

That split maps directly onto this service's real engines: ``raw_text`` is
architecturally declarative (pypdfium2/markitdown convert straight to text,
no per-page recognition needed); ``vl_cpu``/``vl_gpu``/``ocr_classic`` are
paginated (each genuinely processes page-by-page through a recognition/OCR/
layout pipeline). Naming and structuring around this real distinction
instead of inventing new vocabulary.

Adding a future engine (a real docling backend, a firecrawl-based one,
whatever) means implementing one of the two Protocols below and declaring
``supported_formats()``/``accepts()`` — never touching ``routes.py``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from substrate.kernel.document import ExtractionResult


@runtime_checkable
class ExtractionEngine(Protocol):
    """Base surface every engine implements, paginated or declarative."""

    name: str

    def supported_formats(self) -> set[str]:
        """Content-type strings this engine can attempt (e.g.
        ``{"application/pdf"}``). docling's ``supported_formats()``
        classmethod pattern — cheap, no I/O, used by ``routes.py`` to build
        its accept-set without a hardcoded table."""
        ...

    def accepts(self, filename: str, content_type: str) -> bool:
        """Cheap, no-I/O probe: can this engine actually handle *this* file?
        markitdown's ``accepts()`` pattern. Default-implementable as
        ``content_type in self.supported_formats()``, but a real engine may
        also want to look at the filename extension (the same
        octet-stream-content-type case ``convert.py`` already has to
        handle for Office uploads)."""
        ...

    def warmup(self) -> None:
        """Best-effort: run a tiny synthetic input through the engine so
        the first real request doesn't also pay one-time model-load
        latency. Must never raise — a warmup failure just means the first
        real request pays the cost instead, matching this service's
        existing warmup contract."""
        ...

    async def aclose(self) -> None:
        """Release any held resources (a worker pool, an open model, a
        subprocess pool). Always awaited at service shutdown, even for
        engines whose own work is synchronous."""
        ...


class DeclarativeExtractionEngine(ExtractionEngine, Protocol):
    """No recognition pipeline — converts straight to text/markdown.
    Mode ``raw_text`` today."""

    def extract(self, data: bytes, filename: str) -> ExtractionResult: ...

    def extract_batch(
        self, items: list[tuple[bytes, str]]
    ) -> list[ExtractionResult]: ...


class PaginatedExtractionEngine(ExtractionEngine, Protocol):
    """Page-by-page recognition pipeline (layout detection + OCR/VL).
    Modes ``vl_cpu``/``vl_gpu``/``ocr_classic`` today. Async because the VL
    engines await a pooled worker (a local subprocess or a remote HTTP
    call) — see ``llama_pool.py``."""

    async def aextract(self, data: bytes, filename: str) -> ExtractionResult: ...

    async def aextract_batch(
        self, items: list[tuple[bytes, str]]
    ) -> list[ExtractionResult]: ...


__all__ = [
    "ExtractionEngine",
    "DeclarativeExtractionEngine",
    "PaginatedExtractionEngine",
]
