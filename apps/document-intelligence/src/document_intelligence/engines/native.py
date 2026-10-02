"""The built-in reader as an engine — mode ``raw_text``: the library's ``Reader`` (PDFium text layer, native Office/HTML, OCR if there is any).

No layout model and no GPU. It is also what every other mode falls back on, and what reads every non-PDF, non-image format in every mode.
"""

from __future__ import annotations

from substrate.documents import ExtractionResult, ReadLimits, Reader, Strategy


class NativeEngine:
    name = "native"

    def __init__(
        self, *, max_bytes: int = 50 * 1024 * 1024, reader: Reader | None = None
    ) -> None:
        self.reader = reader or Reader(limits=ReadLimits(max_bytes=max_bytes))

    def accepts(self, filename: str, content_type: str) -> bool:
        return True

    def warmup(self) -> None:
        pass

    async def aclose(self) -> None:
        pass

    async def aextract(
        self,
        data: bytes,
        filename: str,
        *,
        content_type: str | None = None,
        strategy: Strategy = "auto",
    ) -> ExtractionResult:
        return await self.reader.read(
            data, filename, content_type=content_type or None, strategy=strategy
        )


__all__ = ["NativeEngine"]
