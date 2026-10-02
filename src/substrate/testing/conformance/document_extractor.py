"""Conformance suite for ``DocumentExtractor``.

A ``Provider`` builds the extractor and makes a sample document with known per-page text in whatever
format that extractor reads. What every extractor must do: surface every page's text in order, number
pages from 1, put a ``<!-- page N -->`` marker before each page of ``markdown`` (in order), name the engine
that did the work, accept every ``strategy`` and a ``content_type`` hint without raising, and **never raise
for a document it cannot read** — a corrupt, empty or hostile file comes back as
``ExtractionResult(success=False, error=…)`` so a chat turn degrades instead of failing.

A provider that can also make a *scanned* document (``scanned() -> (bytes, filename)``: a page that is only
a picture of the text "4417") is held to one more rule, the one that matters most to a user: such a page is
either recognised (its text says 4417) or **reported** in ``needs_ocr`` — never returned silently empty.
"""

from __future__ import annotations

from typing import Protocol

import pytest

from substrate.documents.protocols import DocumentExtractor
from substrate.documents.types import ExtractionResult

PAGES = ["Invoice 4417 total due 120 EUR", "Second page: shipping to Rotterdam", "Third page: terms and conditions"]


class Provider(Protocol):
    paged: bool
    """Whether the format has real page boundaries (a PDF does; a text file is one page)."""
    rejects_garbage: bool
    """Whether arbitrary bytes are a failure for this extractor (a text extractor will happily decode them)."""

    def extractor(self) -> DocumentExtractor: ...

    def document(self, pages: list[str]) -> tuple[bytes, str]:
        """A document whose pages contain exactly ``pages``, and the filename it should be submitted under."""

    def scanned(self) -> tuple[bytes, str] | None:
        """Optional: a one-page document that is only a picture of the text ``Invoice 4417``, and its filename; ``None`` if the
        extractor does not read pictures of text."""


def pdf(pages: list[str]) -> bytes:
    """A minimal, valid text-layer PDF with one line of text per page."""
    objects: list[bytes] = []
    n = len(pages)
    page_ids = [3 + 2 * i for i in range(n)]
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(("<< /Type /Pages /Kids [" + " ".join(f"{p} 0 R" for p in page_ids) + f"] /Count {n} >>").encode())
    font_id = 3 + 2 * n
    for i, text in enumerate(pages):
        content_id = 4 + 2 * i
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_id} 0 R /Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode()
        )
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 14 Tf 72 720 Td ({escaped}) Tj ET".encode()
        objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = b"%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


class DocumentExtractorConformance:
    @pytest.fixture
    def provider(self) -> Provider:  # pragma: no cover - supplied by subclasses
        raise NotImplementedError

    async def read(self, provider: Provider, pages: list[str] = PAGES) -> ExtractionResult:
        data, filename = provider.document(pages)
        return await provider.extractor().read(data, filename)

    async def test_every_pages_text_comes_out_in_order(self, provider: Provider) -> None:
        result = await self.read(provider)
        assert result.success and result.error is None
        body = result.markdown or " ".join(p.text for p in result.pages)
        positions = [body.find(token) for token in ("4417", "Rotterdam", "terms and conditions")]
        assert all(p >= 0 for p in positions), f"text went missing: {positions}"
        assert positions == sorted(positions), "pages were reordered"

    async def test_pages_are_numbered_from_one_in_order(self, provider: Provider) -> None:
        result = await self.read(provider)
        numbers = [p.page_number for p in result.pages]
        assert numbers == sorted(numbers) and len(set(numbers)) == len(numbers)
        if provider.paged:
            assert numbers == [1, 2, 3]
            assert "4417" in result.pages[0].text and "Rotterdam" in result.pages[1].text

    async def test_the_engine_that_did_the_work_is_named(self, provider: Provider) -> None:
        assert (await self.read(provider)).engine

    async def test_the_markdown_marks_each_page_in_order(self, provider: Provider) -> None:
        result = await self.read(provider)
        if not provider.paged or not getattr(provider, "marks_pages", True):
            return
        positions = [result.markdown.find(f"<!-- page {n} -->") for n in (1, 2, 3)]
        assert all(p >= 0 for p in positions), f"missing page markers: {positions}"
        assert positions == sorted(positions)

    @pytest.mark.parametrize("strategy", ["fast", "auto", "hi_res", "ocr_only"])
    async def test_every_strategy_is_accepted_and_a_content_type_is_only_a_hint(self, provider: Provider, strategy: str) -> None:
        data, filename = provider.document(PAGES)
        result = await provider.extractor().read(data, filename, content_type="application/octet-stream", strategy=strategy)  # type: ignore[arg-type]
        assert isinstance(result, ExtractionResult)
        assert result.success or result.error

    async def test_a_scanned_page_is_recognised_or_reported_never_silently_empty(self, provider: Provider) -> None:
        scanned = provider.scanned() if hasattr(provider, "scanned") else None
        if scanned is None:
            pytest.skip("this extractor does not read pictures of text")
        data, filename = scanned
        result = await provider.extractor().read(data, filename)
        assert result.success, result.error
        page = result.pages[0]
        assert "4417" in page.text or page.needs_ocr, f"a scanned page came back empty without saying so: {page.model_dump(exclude={'images'})}"

    async def test_a_corrupt_document_is_a_failure_not_an_exception(self, provider: Provider) -> None:
        data, filename = provider.document(PAGES)
        result = await provider.extractor().read(data[: len(data) // 3], filename)
        assert isinstance(result, ExtractionResult)
        assert result.success or result.error, "a failure must say why"

    async def test_empty_bytes_are_a_failure_not_an_exception(self, provider: Provider) -> None:
        _, filename = provider.document(PAGES)
        result = await provider.extractor().read(b"", filename)
        assert isinstance(result, ExtractionResult)
        assert not result.success or not any(p.text.strip() for p in result.pages)

    async def test_arbitrary_bytes_are_a_failure_not_an_exception(self, provider: Provider) -> None:
        _, filename = provider.document(PAGES)
        result = await provider.extractor().read(bytes(range(256)) * 40, filename)
        assert isinstance(result, ExtractionResult)
        if provider.rejects_garbage:
            assert not result.success and result.error

    @pytest.mark.parametrize("hostile", ["../../etc/passwd.pdf", "a\x00b.pdf", "x' OR '1'='1.pdf", "ünï-çødé 日本語.pdf", "a" * 300 + ".pdf", ".pdf", ""])
    async def test_a_hostile_filename_is_data_not_a_path(self, provider: Provider, hostile: str) -> None:
        data, filename = provider.document(PAGES)
        suffix = filename[filename.rfind("."):]
        name = hostile if hostile.endswith(suffix) else hostile + suffix
        result = await provider.extractor().read(data, name)
        assert isinstance(result, ExtractionResult)


__all__ = ["DocumentExtractorConformance", "Provider", "pdf", "PAGES"]
