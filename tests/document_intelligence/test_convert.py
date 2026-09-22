"""Two-tier document conversion (``convert.py``) — markitdown (Tier 1) and
LibreOffice-headless (Tier 2). Neither is installed in this dev venv (both
are real, non-Python-package-manager-resolvable dependencies — markitdown
needs `uv sync --extra document-intelligence`, soffice needs an apt/system
package), so the markitdown-dependent tests use ``importorskip`` and the
LibreOffice tests exercise the graceful-degradation path (binary missing)
directly, which doesn't need the real binary to test."""

from __future__ import annotations

import asyncio

import pytest

from substrate.kernel.document import ExtractedPage, ExtractionResult
from substrate.runtimes.document_intelligence.service import convert


def test_convertible_content_types_cover_real_office_formats() -> None:
    assert (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        in convert.CONVERTIBLE_CONTENT_TYPES
    )
    assert (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        in convert.CONVERTIBLE_CONTENT_TYPES
    )
    # CSV/XLSX explicitly excluded by scope — that data belongs to the
    # LLM/code-interpreter path, not this service.
    assert "text/csv" not in convert.CONVERTIBLE_CONTENT_TYPES
    assert (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        not in convert.CONVERTIBLE_CONTENT_TYPES
    )


def test_is_thin_below_threshold() -> None:
    thin = ExtractionResult(pages=[ExtractedPage(page_number=1, text="hi")])
    assert convert._is_thin(thin, threshold_chars=100)


def test_is_thin_above_threshold() -> None:
    plump = ExtractionResult(
        pages=[ExtractedPage(page_number=1, text="x" * 500)]
    )
    assert not convert._is_thin(plump, threshold_chars=100)


async def test_convert_via_libreoffice_returns_none_when_binary_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real, guaranteed-to-occur case in any environment without LibreOffice
    installed (like this dev venv) — must degrade gracefully, never raise."""

    async def _raise_not_found(*args, **kwargs):
        raise FileNotFoundError("soffice not found")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _raise_not_found)

    result = await convert.convert_via_libreoffice(b"fake docx bytes", "doc.docx")
    assert result is None


async def test_convert_via_libreoffice_returns_none_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeProc:
        returncode = None

        async def communicate(self):
            raise TimeoutError

        def terminate(self):
            pass

        def kill(self):
            pass

        async def wait(self):
            return None

    async def _fake_exec(*args, **kwargs):
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    result = await convert.convert_via_libreoffice(
        b"fake docx bytes", "doc.docx", timeout_s=0.01
    )
    assert result is None


async def test_convert_office_document_escalates_to_tier2_when_tier1_thin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Injects a fake, thin Tier 1 result and a fake Tier 2 success, without
    needing the real markitdown/soffice binaries — proves the escalation
    wiring itself is correct."""
    thin_result = ExtractionResult(
        pages=[ExtractedPage(page_number=1, text="x")], markdown="x", engine="raw_text"
    )
    monkeypatch.setattr(
        convert, "_tier1_markitdown", lambda data, filename, content_type: thin_result
    )

    called_with = {}

    async def _fake_libreoffice(data, filename, **kwargs):
        called_with["data"] = data
        called_with["filename"] = filename
        return b"%PDF-1.4 fake pdf bytes"

    monkeypatch.setattr(convert, "convert_via_libreoffice", _fake_libreoffice)

    final_result = ExtractionResult(
        pages=[ExtractedPage(page_number=1, text="a full page of real text")],
        markdown="a full page of real text",
        engine="raw_text",
    )
    # _extract_pdf is imported lazily inside convert_office_document from
    # engines.raw_text — patch it at its real definition site.
    import substrate.runtimes.document_intelligence.service.engines.raw_text as raw_text_mod

    monkeypatch.setattr(raw_text_mod, "_extract_pdf", lambda pdf_bytes: final_result)

    result = await convert.convert_office_document(
        b"thin docx bytes", "doc.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    assert called_with["filename"] == "doc.docx"
    assert result is final_result


async def test_convert_office_document_stays_on_tier1_when_not_thin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plump_result = ExtractionResult(
        pages=[ExtractedPage(page_number=1, text="x" * 500)],
        markdown="x" * 500,
        engine="raw_text",
    )
    monkeypatch.setattr(
        convert, "_tier1_markitdown", lambda data, filename, content_type: plump_result
    )

    async def _fail_if_called(*a, **kw):
        raise AssertionError("Tier 2 should not be invoked when Tier 1 is not thin")

    monkeypatch.setattr(convert, "convert_via_libreoffice", _fail_if_called)

    result = await convert.convert_office_document(
        b"real docx bytes", "doc.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert result is plump_result


async def test_convert_office_document_falls_back_to_tier1_when_tier2_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LibreOffice missing/failed -> return Tier 1's (possibly thin) result
    rather than raising -- some real text is better than none."""
    thin_result = ExtractionResult(
        pages=[ExtractedPage(page_number=1, text="x")], markdown="x", engine="raw_text"
    )
    monkeypatch.setattr(
        convert, "_tier1_markitdown", lambda data, filename, content_type: thin_result
    )

    async def _unavailable(*a, **kw):
        return None

    monkeypatch.setattr(convert, "convert_via_libreoffice", _unavailable)

    result = await convert.convert_office_document(
        b"thin docx bytes", "doc.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert result is thin_result


def test_tier1_markitdown_returns_empty_result_when_package_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real, still-real failure mode even with markitdown installed here
    now — an environment without it must still degrade to an empty result,
    not raise. Simulated via a blocked import, since markitdown genuinely
    is installed in this dev venv now (see the real-conversion tests
    below)."""
    import builtins

    real_import = builtins.__import__

    def _blocked_import(name, *args, **kwargs):
        if name == "markitdown":
            raise ImportError("simulated: markitdown not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked_import)

    result = convert._tier1_markitdown(b"data", "doc.docx", "application/msword")
    assert result.pages == []
    assert result.engine == "raw_text"


def _real_docx_bytes(paragraph: str, heading: str) -> bytes:
    import io

    from docx import Document as DocxDocument

    doc = DocxDocument()
    doc.add_paragraph(paragraph)
    doc.add_heading(heading, level=1)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _real_pptx_bytes(title: str, body: str) -> bytes:
    import io

    from pptx import Presentation

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = title
    slide.placeholders[1].text = body
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def test_tier1_markitdown_converts_a_real_docx() -> None:
    """Real markitdown package, real python-docx-built .docx bytes, real
    conversion -- the placeholder this replaces (bit-rot canary from when
    markitdown wasn't installed in this dev venv) fired once `uv sync`
    actually pulled it in; this is the real coverage it asked for."""
    data = _real_docx_bytes("Hello real markitdown test.", "A Heading")

    result = convert._tier1_markitdown(
        data,
        "doc.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    assert result.engine == "raw_text"
    assert len(result.pages) == 1
    assert "Hello real markitdown test." in result.pages[0].text
    assert "A Heading" in result.pages[0].text
    assert result.markdown == result.pages[0].markdown


def test_tier1_markitdown_converts_a_real_pptx() -> None:
    data = _real_pptx_bytes("Real Slide Title", "Real slide body text")

    result = convert._tier1_markitdown(
        data,
        "deck.pptx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )

    assert result.engine == "raw_text"
    assert len(result.pages) == 1
    assert "Real Slide Title" in result.pages[0].text
    assert "Real slide body text" in result.pages[0].text


async def test_convert_office_document_end_to_end_with_a_real_plump_docx() -> None:
    """The full public entry point, real markitdown, no LibreOffice
    escalation needed since the real converted text is well above the
    thin-text threshold."""
    data = _real_docx_bytes(
        "This paragraph has plenty of real text content, well past the "
        "default 100-character thin-text escalation threshold on its own.",
        "A Real Heading",
    )

    result = await convert.convert_office_document(
        data,
        "doc.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    assert result.engine == "raw_text"
    assert "plenty of real text content" in result.markdown
