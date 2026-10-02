"""``ExtractionEngine`` — the layout engines a pod can serve (PaddleOCR-VL, PP-StructureV3) and the built-in reader, behind one method.

Every engine answers with the library's own ``ExtractionResult`` (``substrate.documents``): that type *is* the wire format of
``POST /v1/extract``. The service reads Office, HTML, text and the like with the built-in reader whatever the engine is
(``dispatch.py``); an engine here is asked only about PDFs and images, where layout, tables, charts and recognition matter.

Adding an engine means implementing the five members below — nothing in ``routes.py`` changes.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from substrate.documents import ExtractionResult


@runtime_checkable
class ExtractionEngine(Protocol):
    name: str

    def accepts(self, filename: str, content_type: str) -> bool:
        """Cheap, no-I/O: can this engine take this file? (PDFs and images, for the layout engines.)"""
        ...

    def warmup(self) -> None:
        """Best-effort: run a tiny input so the first real request does not also pay model-load latency. Must never raise."""
        ...

    async def aclose(self) -> None:
        """Release held resources (a worker pool, a model, a subprocess pool); awaited at shutdown."""
        ...

    async def aextract(self, data: bytes, filename: str) -> ExtractionResult: ...


__all__ = ["ExtractionEngine"]
