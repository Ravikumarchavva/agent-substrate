"""``Reader`` — read a document into markdown pages. One entry point; where it runs is a matter of how you construct it.

::

    reader = Reader()                          # in this machine: PDFium, native Office/HTML, OCR if there is any
    reader = Reader("http://doc-intel:8080")   # a document server: layout, tables, chart crops, PaddleOCR-VL
    reader = Reader(engine=my_extractor)       # anything that implements ``DocumentExtractor``

    result = await reader.read(data, "q3.pdf")             # -> ExtractionResult, never raises for a bad document
    result = await reader.read(data, "scan.pdf", strategy="ocr_only")

All three answer with the same ``ExtractionResult``: pages with ``<!-- page N -->`` markers in ``markdown``, headings as ``#``, tables as
GFM, figures as ``![…](cid:id)`` with their bytes in ``pages[n].images``, and ``needs_ocr`` listing any page that has pixels but no
text and could not be recognised.

**The built-in reader** reads PDF (text layer, headings, figures), DOCX, PPTX, XLSX, ODT, ODP, ODS, HTML, Markdown, plain text, CSV/TSV and
JSON, and runs OCR (RapidOCR if installed, else the ``tesseract`` program if on the path) on pages with no text. By default each
document is read in an **isolated worker process** with a memory cap, a file-write ban and a wall-clock limit (``isolate=False`` reads in
a thread instead): a hostile or broken file ends its worker, not your process. Limits are ``ReadLimits``.

``strategy``: ``fast`` never OCRs · ``auto`` OCRs only pages with no text · ``ocr_only`` OCRs every page · ``hi_res`` also runs a layout
model — only a document server has one, so in-process it degrades to ``auto`` and says so (``degraded_from``).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence

from substrate.documents.protocols import DocumentExtractor, Ocr
from substrate.documents.reading.engine import read_document
from substrate.documents.reading.ocr import resolve_ocr
from substrate.documents.reading.pool import WorkerFailure, get_pool
from substrate.documents.types import ExtractionResult, ReadLimits, Strategy

logger = logging.getLogger(__name__)

_STRATEGIES = ("fast", "auto", "hi_res", "ocr_only")


class Reader:
    def __init__(
        self,
        location: str | None = None,
        *,
        engine: DocumentExtractor | None = None,
        ocr: str | Ocr | None = "auto",
        languages: Sequence[str] = ("eng",),
        limits: ReadLimits | None = None,
        isolate: bool = True,
        workers: int | None = None,
        fallback: bool = True,
        api_key: str = "",
        timeout: float = 120.0,
    ) -> None:
        """``location`` is a document server's URL; ``engine`` is any extractor; neither means the built-in reader.

        ``ocr``: ``"auto"`` (RapidOCR, else Tesseract, else none), ``"tesseract"``, ``"rapidocr"``, ``None``, or an ``Ocr`` instance
        (which needs ``isolate=False``: a worker rebuilds its engine from a name). ``fallback`` (with a ``location``): when the server
        is unreachable, read with the built-in reader instead of failing."""
        if location is not None and engine is not None:
            raise ValueError("pass a location or an engine, not both")
        if isolate and ocr is not None and not isinstance(ocr, str):
            raise ValueError(
                "a custom Ocr instance runs in this process: pass isolate=False, or name an engine ('tesseract', 'rapidocr')"
            )
        self.limits = limits or ReadLimits()
        self.languages = tuple(languages)
        self._engine = engine
        self._location = location
        self._ocr_spec = ocr
        self._isolate = isolate
        self._workers = workers
        self._fallback = fallback
        self._api_key = api_key
        self._timeout = timeout
        self._ocr_obj: Ocr | None = None
        self._ocr_resolved = False
        self._remote = None
        if location is not None:
            from substrate.documents.remote import RemoteExtractor  # noqa: PLC0415

            self._remote = RemoteExtractor(location, api_key=api_key, timeout=timeout)

    # -- the one method --------------------------------------------------------------------------------------------------

    async def read(
        self,
        data: bytes,
        filename: str = "",
        *,
        content_type: str | None = None,
        strategy: Strategy = "auto",
    ) -> ExtractionResult:
        """Read ``data``. Never raises for a document it cannot read: the result says why (``success=False``, ``error``)."""
        if strategy not in _STRATEGIES:
            raise ValueError(f"strategy must be one of {_STRATEGIES}, got {strategy!r}")
        if self._engine is not None:
            return await self._engine.read(
                data, filename, content_type=content_type, strategy=strategy
            )
        if self._remote is not None:
            return await self._read_remote(data, filename, content_type, strategy)
        return await self._read_here(data, filename, content_type, strategy)

    # -- the built-in reader -----------------------------------------------------------------------------------------------

    def _timeout_for(self, data: bytes) -> float:
        limits = self.limits
        pages = min(limits.max_pages, max(1, len(data) // 50_000))
        return min(limits.max_timeout_s, limits.timeout_s + limits.per_page_s * pages)

    async def _read_here(
        self, data: bytes, filename: str, content_type: str | None, strategy: str
    ) -> ExtractionResult:
        degraded = None
        if strategy == "hi_res":
            strategy, degraded = "auto", "hi_res"
        timeout_s = self._timeout_for(data)
        if self._isolate:
            header = {
                "filename": filename,
                "content_type": content_type,
                "strategy": strategy,
                "limits": self.limits.model_dump(),
                "ocr": "none" if self._ocr_spec is None else self._ocr_spec,
                "languages": list(self.languages),
                "timeout_s": timeout_s,
            }
            try:
                payload = await asyncio.to_thread(
                    get_pool(self._workers).run, header, data, timeout_s=timeout_s
                )
                result = ExtractionResult.model_validate_json(payload)
            except WorkerFailure as exc:
                result = ExtractionResult(
                    success=False, error=str(exc), engine="native"
                )
            except (ValueError, json.JSONDecodeError) as exc:
                result = ExtractionResult(
                    success=False,
                    error=f"the reader process answered with something unreadable: {exc}",
                    engine="native",
                )
        else:
            if not self._ocr_resolved:
                self._ocr_obj = resolve_ocr(self._ocr_spec)  # type: ignore[arg-type]
                self._ocr_resolved = True
            import time  # noqa: PLC0415

            result = await asyncio.to_thread(
                read_document,
                data,
                filename,
                content_type,
                strategy=strategy,
                limits=self.limits,
                ocr=self._ocr_obj,
                languages=self.languages,
                deadline=time.monotonic() + timeout_s,
            )
        if degraded:
            result = result.model_copy(
                update={
                    "degraded_from": degraded,
                    "warnings": [
                        *result.warnings,
                        "hi_res needs a layout model, which only a document server has: read with strategy 'auto'",
                    ],
                }
            )
        return result

    async def _read_remote(
        self, data: bytes, filename: str, content_type: str | None, strategy: str
    ) -> ExtractionResult:
        assert self._remote is not None
        result = await self._remote.read(
            data, filename, content_type=content_type, strategy=strategy
        )  # type: ignore[arg-type]
        if (
            result.success
            or not self._fallback
            or result.degraded_from != "unreachable"
        ):
            return result
        local = await self._read_here(data, filename, content_type, strategy)
        return local.model_copy(
            update={
                "degraded_from": self._location,
                "warnings": [
                    *local.warnings,
                    f"{self._location} was unreachable: read locally",
                ],
            }
        )

    async def aclose(self) -> None:
        """Nothing is held open between reads; present so a ``Reader`` can be closed like every other service client."""


__all__ = ["Reader"]
